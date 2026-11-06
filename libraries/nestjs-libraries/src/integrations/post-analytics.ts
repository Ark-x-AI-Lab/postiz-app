import type { AnalyticsData, PostAnalyticsContext } from './social/social.integrations.interface';

type MetricKey = NonNullable<AnalyticsData['metricKey']>;
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object'
  && !Array.isArray(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value));
const own = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key);
export function explicitCount(value: unknown): number | undefined {
  const count = typeof value === 'number' ? value
    : typeof value === 'string' && /^(0|[1-9]\d{0,15})$/.test(value) ? Number(value) : NaN;
  return Number.isSafeInteger(count) && count >= 0 ? count : undefined;
}
function breakdownCount(value: unknown): number | undefined {
  if (!analyticsBody(value)) return undefined;
  const values = Object.values(value);
  if (!values.length || values.length > 100) return undefined;
  let total = 0;
  for (const value of values) {
    const count = explicitCount(value);
    if (count === undefined || !Number.isSafeInteger(total + count)) return undefined;
    total += count;
  }
  return total;
}
export function analyticsBody(value: unknown): value is Record<string, unknown> {
  return record(value) && !['error', 'errors', 'serviceErrorCode'].some(key => own(value, key))
    && !(typeof value.status === 'number' && value.status >= 400);
}

/** This checks the saved dispatch path, not remote bytes or a Media.type default. */
export function savedPostAnalyticsContext(post: { state?: unknown; image?: unknown; settings?: unknown }, provider: string): PostAnalyticsContext | undefined {
  if (!['facebook', 'instagram', 'instagram-standalone', 'linkedin-page'].includes(provider) || post.state !== 'PUBLISHED') return undefined;
  try {
    if (typeof post.image !== 'string' || post.image.length > 16384 || typeof post.settings !== 'string' || post.settings.length > 16384) return undefined;
    const media: unknown = JSON.parse(post.image), settings: unknown = JSON.parse(post.settings);
    if (!Array.isArray(media) || media.length !== 1 || !record(media[0]) || !record(settings) || settings.__type !== provider) return undefined;
    const image = media[0];
    if (typeof image.id !== 'string' || !/^[A-Za-z0-9_-]{1,200}$/.test(image.id)
      || typeof image.path !== 'string' || image.path.length > 2048 || /[\s\\%?#]/.test(image.path)
      || !/\.(png|jpe?g|webp)$/i.test(image.path) || /\.(mp4|pdf)/i.test(image.path)
      || (own(image, 'type') && image.type !== 'image') || own(image, 'buffer') || own(image, 'thumbnailTimestamp')) return undefined;
    const url = new URL(image.path, 'https://saved-media.invalid');
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password
      || (!image.path.startsWith('/') && !/^https?:\/\//.test(image.path))) return undefined;
    if (provider === 'instagram' || provider === 'instagram-standalone') {
      if (settings.post_type !== 'post' || (settings.is_trial_reel !== undefined && settings.is_trial_reel !== false) || settings.audio) return undefined;
    } else if (provider === 'facebook') {
      if ((settings.post_type !== undefined && settings.post_type !== 'post') || settings.url) return undefined;
    } else if (settings.post_as_images_carousel !== undefined && settings.post_as_images_carousel !== false) return undefined;
    return { mediaKind: 'single-image', provider: provider as PostAnalyticsContext['provider'] };
  } catch { return undefined; }
}

/** Identical aggregate copies collapse; conflicts or invalid explicit values cannot win by order. */
function collector(today: string, proven: boolean) {
  const counts = new Map<MetricKey, { label: string; count: number; lifetime: boolean }>();
  const blocked = new Set<MetricKey>();
  return {
    add(key: MetricKey, label: string, count: number | undefined, lifetime = true) {
      if (blocked.has(key)) return;
      const previous = counts.get(key);
      if (count === undefined || (previous && previous.count !== count)) { blocked.add(key); counts.delete(key); return; }
      counts.set(key, { label, count, lifetime: lifetime && (previous?.lifetime ?? true) });
    },
    rows(numeric = false): AnalyticsData[] {
      // LinkedIn's existing runtime totals are numeric despite the shared legacy string declaration.
      return [...counts].map(([metricKey, item]) => ({ label: item.label, percentageChange: 0,
        data: [{ total: numeric ? item.count : String(item.count), date: today }],
        ...(proven && item.lifetime ? { metricKey, measurementBasis: 'lifetime' as const, provenance: 'explicit' as const } : {}),
      })) as unknown as AnalyticsData[];
    },
  };
}
const facebookMetrics: Record<string, { key: MetricKey; label: string; breakdown?: boolean }> = {
  post_total_media_view_unique: { key: 'reach', label: 'Reach' },
  post_reactions_by_type_total: { key: 'reactions', label: 'Reactions', breakdown: true },
  post_clicks: { key: 'clicks', label: 'Clicks' },
  post_clicks_by_type: { key: 'clicksByType', label: 'Clicks by Type', breakdown: true },
};
const instagramMetrics: Record<string, { key: MetricKey; label: string; breakdown?: boolean }> = {
  views: { key: 'views', label: 'Views' }, reach: { key: 'reach', label: 'Reach' }, saved: { key: 'saves', label: 'Saves' },
  likes: { key: 'likes', label: 'Likes' }, comments: { key: 'comments', label: 'Comments' }, shares: { key: 'shares', label: 'Shares' },
};
export function metaPostAnalytics(body: unknown, provider: 'facebook' | 'instagram' | 'instagram-standalone', today: string, context?: PostAnalyticsContext): AnalyticsData[] {
  if (!analyticsBody(body) || !Array.isArray(body.data) || body.data.length > 100) return [];
  const metrics = provider === 'facebook' ? facebookMetrics : instagramMetrics;
  const result = collector(today, context?.mediaKind === 'single-image' && context.provider === provider);
  for (const item of body.data) {
    if (!record(item) || typeof item.name !== 'string' || !own(metrics, item.name)) continue;
    const metric = metrics[item.name];
    const point = Array.isArray(item.values) && item.values.length === 1 && analyticsBody(item.values[0]) ? item.values[0] : undefined;
    const count = analyticsBody(item) && point && own(point, 'value') ? (metric.breakdown ? breakdownCount(point.value) : explicitCount(point.value)) : undefined;
    result.add(metric.key, metric.label, count, item.period === undefined || item.period === 'lifetime');
  }
  return result.rows();
}

const linkedinMetrics: Record<string, { key: MetricKey; label: string }> = {
  impressionCount: { key: 'impressions', label: 'Impressions' }, uniqueImpressionsCount: { key: 'uniqueImpressions', label: 'Unique Impressions' },
  clickCount: { key: 'clicks', label: 'Clicks' }, likeCount: { key: 'likes', label: 'Likes' },
  commentCount: { key: 'comments', label: 'Comments' }, shareCount: { key: 'shares', label: 'Shares' },
};
export function linkedinPostAnalytics(body: unknown, social: unknown, today: string, integrationId: string, postId: string, context?: PostAnalyticsContext): AnalyticsData[] {
  if (!analyticsBody(body) || !Array.isArray(body.elements) || body.elements.length > 100) return [];
  const result = collector(today, context?.mediaKind === 'single-image' && context.provider === 'linkedin-page');
  for (const item of body.elements) {
    if (!record(item) || (own(item, 'share') && item.share !== postId)
      || (own(item, 'organizationalEntity') && item.organizationalEntity !== `urn:li:organization:${integrationId}`)) return [];
    // An unscoped failed element cannot identify which aggregate copies it invalidates.
    if (!own(item, 'totalShareStatistics')) { if (!analyticsBody(item)) return []; continue; }
    if (!record(item.totalShareStatistics)) return [];
    const healthy = analyticsBody(item) && analyticsBody(item.totalShareStatistics);
    for (const [field, metric] of Object.entries(linkedinMetrics)) {
      if (own(item.totalShareStatistics, field)) result.add(metric.key, metric.label, healthy ? explicitCount(item.totalShareStatistics[field]) : undefined, item.timeRange === undefined);
    }
    // Error-only aggregates have no metric identity: retaining other copies would be ambiguous.
    if (!healthy && !Object.keys(linkedinMetrics).some(field => own(item.totalShareStatistics as object, field))) return [];
  }
  if (!body.elements.length && analyticsBody(social)) {
    for (const [summary, field, key, label] of [['likesSummary', 'totalLikes', 'likes', 'Likes'], ['commentsSummary', 'totalFirstLevelComments', 'comments', 'Comments']] as const) {
      const value = social[summary];
      if (analyticsBody(value) && own(value, field)) result.add(key, label, explicitCount(value[field]));
    }
  }
  return result.rows(true);
}
