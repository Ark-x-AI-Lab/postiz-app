const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../../../..');
let ts;
try { ts = require('typescript'); }
catch { ts = createRequire(path.resolve(root, '../arkx-social-studio/package.json'))('typescript'); }
const sentinel = 'SYNTHETIC_PRIVATE_ANALYTICS_TOKEN';
const today = '2026-11-06';
const files = {
  helper: 'libraries/nestjs-libraries/src/integrations/post-analytics.ts',
  facebook: 'libraries/nestjs-libraries/src/integrations/social/facebook.provider.ts',
  instagram: 'libraries/nestjs-libraries/src/integrations/social/instagram.provider.ts',
  standalone: 'libraries/nestjs-libraries/src/integrations/social/instagram.standalone.provider.ts',
  linkedin: 'libraries/nestjs-libraries/src/integrations/social/linkedin.page.provider.ts',
  service: 'libraries/nestjs-libraries/src/database/prisma/posts/posts.service.ts',
};

// Same source-transpile/substitute pattern as linkedin.oauth.diagnostics.test.cjs.
// No real provider, database, Redis, OAuth client, application bootstrap or environment is loaded.
function fixture(runtime = {}) {
  const cache = {}, calls = [], logs = [], redis = [];
  let respond = async () => { throw new Error('Unexpected synthetic fetch'); };
  const decorator = () => () => undefined;
  const dayjs = Object.assign(() => ({ format: () => today, isBefore: () => false }), { extend() {} });
  function load(key) {
    if (cache[key]) return cache[key];
    const file = path.join(root, files[key]), module = { exports: {} };
    const dependency = name => {
      if (name.endsWith('post-analytics')) return load('helper');
      if (name.endsWith('instagram.provider')) return load('instagram');
      if (name.endsWith('facebook.provider')) return load('facebook');
      if (name.endsWith('linkedin.provider')) return { LinkedinProvider: class {} };
      if (name.endsWith('linkedin.oauth.diagnostics')) return { LinkedInOAuthDiagnostics: class {} };
      if (name.endsWith('social.abstract')) return { SocialAbstract: class {}, RefreshToken: class extends Error {} };
      if (name.endsWith('redis.service')) return { ioRedis: { set: async (...args) => { redis.push(args); } } };
      if (name === 'dayjs') return dayjs;
      return new Proxy({}, { get: (_, name) => name === '__esModule' ? false : decorator });
    };
    const compiled = ts.transpileModule(fs.readFileSync(file, 'utf8'), { fileName: file, reportDiagnostics: true,
      compilerOptions: { target: ts.ScriptTarget.ES2021, module: ts.ModuleKind.CommonJS, experimentalDecorators: true, esModuleInterop: true } });
    assert.deepEqual(compiled.diagnostics.filter(item => item.category === ts.DiagnosticCategory.Error), []);
    new Function('require', 'module', 'exports', 'fetch', 'console', 'setTimeout', 'clearTimeout', compiled.outputText)(dependency, module, module.exports,
      async (url, init) => { assert.equal(init?.method || 'GET', 'GET'); calls.push({ url, init }); return respond(url, init); },
      { warn: (...args) => logs.push(args), error: (...args) => logs.push(args), log: (...args) => logs.push(args) },
      runtime.setTimeout || setTimeout, runtime.clearTimeout || clearTimeout);
    cache[key] = module.exports;
    return module.exports;
  }
  return { load, calls, logs, redis, set respond(value) { respond = value; } };
}
const helper = fixture().load('helper');
function saved(provider, patch = {}) {
  return { state: 'PUBLISHED', image: JSON.stringify([{ id: 'fixture-image', path: 'https://media.fixture.invalid/uploads/image.png' }]),
    settings: JSON.stringify({ __type: provider, ...(provider.startsWith('instagram') ? { post_type: 'post' } : {}) }), ...patch };
}
const context = provider => helper.savedPostAnalyticsContext(saved(provider), provider);
const metric = (name, value, extra = {}) => ({ name, values: [{ value }], ...extra });
const response = (body, ok = true) => ({ ok, json: async () => structuredClone(body) });
const byKey = rows => Object.fromEntries(rows.map(row => [row.metricKey, row.data[0].total]));
function proven(rows) {
  assert.ok(rows.length);
  for (const row of rows) { assert.equal(row.measurementBasis, 'lifetime'); assert.equal(row.provenance, 'explicit'); assert.equal(row.data.length, 1); }
}

test('explicit counts retain real zero and refuse coercion, negatives, fractions and unsafe magnitudes', () => {
  for (const value of [0, '0', 7, '7', Number.MAX_SAFE_INTEGER, String(Number.MAX_SAFE_INTEGER)]) assert.equal(helper.explicitCount(value), Number(value));
  for (const value of [null, undefined, '', ' ', true, false, [], {}, -1, '-1', 1.5, '1.5', '1e3', '0x10', '01', NaN, Infinity, 9007199254740992, '9007199254740992', '1'.repeat(10000)]) assert.equal(helper.explicitCount(value), undefined);
});
test('saved media/settings establish only conservative published single-image dispatch paths', () => {
  for (const provider of ['facebook', 'instagram', 'instagram-standalone', 'linkedin-page']) assert.deepEqual(context(provider), { mediaKind: 'single-image', provider });
  const variants = [
    { state: 'QUEUE' }, { image: null }, { image: '{broken' }, { image: '[]' }, { image: 'x'.repeat(16385) },
    { image: JSON.stringify([{ id: 'image', path: '/image.png' }, { id: 'other', path: '/other.png' }]) },
    ...['/video.mp4', '/movie.mp4.png', '/document.pdf.png', '/unknown', '/animation.gif', '/image.png?type=video', '/image%2epng', 'javascript:image.png'].map(path => ({ image: JSON.stringify([{ id: 'image', path }]) })),
    { image: JSON.stringify([{ id: 'image', path: '/image.png', type: 'video' }]) },
    { image: JSON.stringify([{ id: 'image', path: '/image.png', thumbnailTimestamp: 1 }]) },
    { settings: null }, { settings: '{broken' }, { settings: '{}' }, { settings: JSON.stringify({ __type: 'other' }) },
  ];
  for (const provider of ['facebook', 'instagram', 'instagram-standalone', 'linkedin-page']) for (const patch of variants) assert.equal(helper.savedPostAnalyticsContext(saved(provider, patch), provider), undefined);
  for (const provider of ['facebook', 'instagram', 'instagram-standalone']) assert.equal(helper.savedPostAnalyticsContext(saved(provider, { settings: JSON.stringify({ __type: provider, post_type: 'story' }) }), provider), undefined);
  for (const post_as_images_carousel of [true, 'false']) assert.equal(helper.savedPostAnalyticsContext(saved('linkedin-page', { settings: JSON.stringify({ __type: 'linkedin-page', post_as_images_carousel }) }), 'linkedin-page'), undefined);
  assert.equal(helper.savedPostAnalyticsContext(saved('instagram', { settings: JSON.stringify({ __type: 'instagram' }) }), 'instagram'), undefined);
  assert.equal(helper.savedPostAnalyticsContext(saved('instagram', { settings: JSON.stringify({ __type: 'instagram', post_type: 'post', is_trial_reel: true }) }), 'instagram'), undefined);
  assert.equal(helper.savedPostAnalyticsContext(saved('linkedin'), 'linkedin'), undefined);
});
test('Meta maps explicit single aggregate counts without inventing labels, rates or missing zero values', () => {
  const fb = helper.metaPostAnalytics({ data: [metric('post_total_media_view_unique', '0'), metric('post_reactions_by_type_total', { like: 0, love: '2' }), metric('post_clicks', 3), metric('post_clicks_by_type', { link: 3 })] }, 'facebook', today, context('facebook'));
  proven(fb); assert.deepEqual(byKey(fb), { reach: '0', reactions: '2', clicks: '3', clicksByType: '3' });
  assert.equal(fb[0].label, 'Reach'); assert.ok(!fb.some(row => row.label === 'Likes'));
  const ig = helper.metaPostAnalytics({ data: ['views', 'reach', 'saved', 'likes', 'comments', 'shares'].map(name => metric(name, 0)) }, 'instagram', today, context('instagram'));
  proven(ig); assert.deepEqual(byKey(ig), { views: '0', reach: '0', saves: '0', likes: '0', comments: '0', shares: '0' });
  for (const value of [{}, null, { like: -1 }, { like: 1, love: 'bad' }, { like: Number.MAX_SAFE_INTEGER, love: 1 }]) assert.deepEqual(helper.metaPostAnalytics({ data: [metric('post_reactions_by_type_total', value)] }, 'facebook', today, context('facebook')), []);
});
test('Meta collapses identical duplicates and omits conflicts, malformed snapshots and error-shaped data', () => {
  const run = data => helper.metaPostAnalytics({ data }, 'instagram', today, context('instagram'));
  assert.equal(run([metric('reach', 0), metric('reach', '0')]).length, 1);
  for (const duplicate of [metric('reach', 1), metric('reach', null), { name: 'reach', values: [{ value: 0 }, { value: 0 }] }]) assert.deepEqual(run([metric('reach', 0), duplicate]), []);
  for (const body of [{ error: {}, data: [metric('reach', 0)] }, { errors: [], data: [] }, { status: 403, data: [] }, [], null]) assert.deepEqual(helper.metaPostAnalytics(body, 'instagram', today, context('instagram')), []);
  assert.deepEqual(run([metric('engagement', 4)]), []);
});
test('unknown/video/story context and non-lifetime points keep legacy fields without new provenance', () => {
  for (const ctx of [undefined, context('facebook')]) {
    const rows = helper.metaPostAnalytics({ data: [metric('reach', 0)] }, 'instagram', today, ctx);
    assert.equal(rows[0].data[0].total, '0'); assert.equal(rows[0].metricKey, undefined); assert.equal(rows[0].measurementBasis, undefined); assert.equal(rows[0].provenance, undefined);
  }
  const windowed = helper.metaPostAnalytics({ data: [metric('reach', 2, { period: 'day' })] }, 'instagram', today, context('instagram'));
  assert.equal(windowed[0].provenance, undefined);
});
test('Meta nested errors poison only their known metric, including clean duplicate copies in either order', () => {
  const markers = [{ error: { code: 190 } }, { errors: [] }, { serviceErrorCode: 0 }, { status: 403 }];
  for (const provider of ['instagram', 'instagram-standalone', 'facebook']) {
    const name = provider === 'facebook' ? 'post_total_media_view_unique' : 'reach';
    const healthyName = provider === 'facebook' ? 'post_clicks' : 'likes';
    const healthyKey = provider === 'facebook' ? 'clicks' : 'likes';
    const run = data => helper.metaPostAnalytics({ data }, provider, today, context(provider));
    const healthy = run([metric(name, 0, { status: 200 }), metric(healthyName, 0)]);
    proven(healthy); assert.equal(healthy.length, 2);
    for (const marker of markers) for (const bad of [metric(name, 11, marker), { name, values: [{ value: 12, ...marker }] }]) {
      assert.deepEqual(run([bad]), []);
      for (const duplicates of [[metric(name, 0), bad], [bad, metric(name, 0)]]) {
        const rows = run([...duplicates, metric(healthyName, 0)]);
        proven(rows); assert.deepEqual(byKey(rows), { [healthyKey]: '0' });
      }
    }
  }
  for (const marker of markers) {
    const rows = helper.metaPostAnalytics({ data: [metric('post_reactions_by_type_total', { like: 2, ...marker }), metric('post_clicks', 0)] }, 'facebook', today, context('facebook'));
    proven(rows); assert.deepEqual(byKey(rows), { clicks: '0' });
  }
});
test('LinkedIn retains only present valid numeric counts, including zero, and omits the rate field', () => {
  const body = { elements: [{ totalShareStatistics: { impressionCount: 0, uniqueImpressionsCount: 0, clickCount: 0, likeCount: 0, commentCount: 0, shareCount: 0, engagement: 0 } }] };
  const rows = helper.linkedinPostAnalytics(body, undefined, today, 'page', 'urn:li:share:fixture', context('linkedin-page'));
  proven(rows); assert.deepEqual(byKey(rows), { impressions: 0, uniqueImpressions: 0, clicks: 0, likes: 0, comments: 0, shares: 0 });
  assert.ok(!rows.some(row => row.label === 'Engagement'));
  assert.deepEqual(helper.linkedinPostAnalytics({ elements: [{ totalShareStatistics: { likeCount: -1, commentCount: null } }] }, undefined, today, 'page', 'post', context('linkedin-page')), []);
  const fallback = helper.linkedinPostAnalytics({ elements: [] }, { likesSummary: { totalLikes: 0 } }, today, 'page', 'post', context('linkedin-page'));
  assert.deepEqual(byKey(fallback), { likes: 0 });
});
test('LinkedIn duplicate aggregates are never summed; conflicts, foreign identities and error fallbacks stay unavailable', () => {
  const run = (elements, social) => helper.linkedinPostAnalytics({ elements }, social, today, 'page', 'post', context('linkedin-page'));
  assert.deepEqual(byKey(run([{ totalShareStatistics: { likeCount: 2 } }, { totalShareStatistics: { likeCount: 2 } }])), { likes: 2 });
  assert.deepEqual(run([{ totalShareStatistics: { likeCount: 0 } }, { totalShareStatistics: { likeCount: 1 } }]), []);
  for (const identity of [{ share: 'wrong' }, { organizationalEntity: 'urn:li:organization:wrong' }]) assert.deepEqual(run([{ ...identity, totalShareStatistics: { likeCount: 1 } }]), []);
  for (const social of [{}, { status: 403, likesSummary: { totalLikes: 0 } }, { error: 'private', commentsSummary: { totalFirstLevelComments: 0 } }]) assert.deepEqual(run([], social), []);
  const rows = run([{ timeRange: { start: 1 }, totalShareStatistics: { likeCount: 2 } }]); assert.equal(rows[0].provenance, undefined);
});
test('LinkedIn failed elements/statistics poison identified counts without dropping independent healthy metrics', () => {
  const run = elements => helper.linkedinPostAnalytics({ elements }, undefined, today, 'page', 'post', context('linkedin-page'));
  const clean = { totalShareStatistics: { likeCount: 0 } };
  const healthy = run([{ status: 200, totalShareStatistics: { status: 200, likeCount: 0, commentCount: 0 } }]);
  proven(healthy); assert.deepEqual(byKey(healthy), { likes: 0, comments: 0 });
  for (const marker of [{ error: { status: 403 } }, { errors: [] }, { serviceErrorCode: 0 }, { status: 403 }]) {
    for (const bad of [{ ...marker, totalShareStatistics: { likeCount: 13 } }, { totalShareStatistics: { ...marker, likeCount: 13 } }]) {
      assert.deepEqual(run([bad]), []);
      for (const duplicates of [[clean, bad], [bad, clean]]) {
        const rows = run([...duplicates, { totalShareStatistics: { commentCount: 0 } }]);
        proven(rows); assert.deepEqual(byKey(rows), { comments: 0 });
      }
    }
    for (const unscoped of [marker, { totalShareStatistics: marker }]) {
      assert.deepEqual(run([clean, unscoped]), []);
      assert.deepEqual(run([unscoped, clean]), []);
    }
  }
});
test('LinkedIn failed social summaries omit only that summary and keep independent explicit zero', () => {
  const run = social => helper.linkedinPostAnalytics({ elements: [] }, social, today, 'page', 'post', context('linkedin-page'));
  const summaries = { likesSummary: { totalLikes: 0, status: 200 }, commentsSummary: { totalFirstLevelComments: 0, status: 200 } };
  const healthy = run(summaries); proven(healthy); assert.deepEqual(byKey(healthy), { likes: 0, comments: 0 });
  for (const marker of [{ error: { status: 403 } }, { errors: [] }, { serviceErrorCode: 0 }, { status: 403 }]) {
    for (const [summary, field, survivingKey] of [['likesSummary', 'totalLikes', 'comments'], ['commentsSummary', 'totalFirstLevelComments', 'likes']]) {
      assert.deepEqual(run({ [summary]: { [field]: 14, ...marker } }), []);
      const rows = run({ ...summaries, [summary]: { [field]: 14, ...marker } });
      proven(rows); assert.deepEqual(byKey(rows), { [survivingKey]: 0 });
    }
  }
});
test('actual Facebook and Instagram providers use checked mappings with no added metadata or write requests', async () => {
  for (const [key, provider, id, body] of [
    ['facebook', 'facebook', 'page_post', { data: [metric('post_total_media_view_unique', 0)] }],
    ['instagram', 'instagram', 'media', { data: [metric('likes', 0)] }],
    ['standalone', 'instagram-standalone', 'media', { data: [metric('likes', 0)] }],
  ]) {
    const f = fixture(); f.respond = async () => response(body);
    const Class = Object.values(f.load(key)).find(value => typeof value === 'function');
    const rows = await new Class().postAnalytics('page', sentinel, id, 30, context(provider));
    proven(rows); assert.equal(f.calls.length, 1); assert.ok(f.calls[0].url.includes('/insights?metric='));
    if (key === 'standalone') assert.equal(new URL(f.calls[0].url).hostname, 'graph.instagram.com');
  }
});
test('actual providers reject failed HTTP and error bodies and never log private transport diagnostics', async () => {
  for (const key of ['facebook', 'instagram', 'linkedin']) for (const failure of ['http', 'body', 'throw']) {
    const f = fixture();
    f.respond = async () => {
      if (failure === 'throw') throw new Error(sentinel);
      if (failure === 'http') return { ok: false, json: async () => { assert.fail('Rejected bodies must not be read'); } };
      return response({ error: sentinel, data: [metric('likes', 0)], elements: [{ totalShareStatistics: { likeCount: 0 } }] });
    };
    const Class = Object.values(f.load(key)).find(value => typeof value === 'function');
    assert.deepEqual(await new Class().postAnalytics('page', sentinel, 'page_post', 30, context(key === 'linkedin' ? 'linkedin-page' : key)), []);
    assert.equal(f.calls.length, 1); assert.equal(JSON.stringify(f.logs).includes(sentinel), false);
  }
});
test('actual LinkedIn Page preserves explicit zero and uses only the existing aggregate/social endpoints', async () => {
  for (const fallback of [false, true]) {
    const f = fixture(); f.respond = async url => response(url.includes('/socialActions/') ? { commentsSummary: { totalFirstLevelComments: 0 } } : { elements: fallback ? [] : [{ totalShareStatistics: { likeCount: 0 } }] });
    const provider = new (f.load('linkedin').LinkedinPageProvider)();
    const rows = await provider.postAnalytics('page', sentinel, 'post', 30, context('linkedin-page'));
    proven(rows); assert.deepEqual(byKey(rows), fallback ? { comments: 0 } : { likes: 0 });
    assert.equal(f.calls.length, fallback ? 2 : 1);
  }
});
test('actual Facebook video path remains legacy/unproven even with unrelated single-image context', async () => {
  const f = fixture(); f.respond = async () => response({ data: [metric('total_video_views', 0)] });
  const rows = await new (f.load('facebook').FacebookProvider)().postAnalytics('page', sentinel, 'bareVideo', 30, context('facebook'));
  assert.equal(rows[0].label, 'Views'); assert.equal(rows[0].provenance, undefined); assert.equal(f.calls.length, 1);
});
test('actual service derives optional context only from its org-scoped saved post and forwards no content or query flag', async () => {
  for (const provider of ['facebook', 'instagram', 'instagram-standalone', 'linkedin-page']) for (const eligible of [true, false]) {
    const f = fixture(), args = [], reads = [];
    const post = { ...saved(provider), releaseId: 'release', integration: { providerIdentifier: provider, internalId: 'native-page', token: sentinel } };
    if (!eligible) post.image = JSON.stringify([{ id: 'fixture-video', path: '/video.mp4', type: 'image' }]);
    const service = Object.create(f.load('service').PostsService.prototype);
    service._postRepository = { getPostById: async (...value) => { reads.push(value); return post; } };
    service._integrationManager = { getSocialIntegration: () => ({ postAnalytics: async (...value) => { args.push(value); return []; } }) };
    service.resolveRelease = async () => ({ releaseId: 'resolved-release' });
    await service.checkPostAnalytics('org-fixture', 'local-post', 30, false, { mediaKind: 'single-image', provider });
    assert.deepEqual(reads, [['local-post', 'org-fixture']]);
    assert.deepEqual(args, [['native-page', sentinel, 'resolved-release', 30, eligible ? context(provider) : undefined]]);
    assert.equal(f.redis.length, 1); assert.equal(f.calls.length, 0);
  }
});


const facebookPostId = '123_456';
const summaryBodies = () => [
  { id: facebookPostId, reactions: { data: [], summary: { total_count: 0 } } },
  { id: facebookPostId, comments: { data: [], summary: { total_count: 2 } } },
  { id: facebookPostId, shares: { count: 3 } },
];
const jsonResponse = body => new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
const batchIndex = url => {
  const parsed = new URL(url), fields = parsed.searchParams.get('fields');
  return parsed.pathname.endsWith('/insights') ? -1 : fields.includes('reactions') ? 0 : fields.includes('comments') ? 1 : 2;
};
const insightBody = () => ({ data: [metric('post_total_media_view_unique', 0), metric('post_reactions_by_type_total', { love: 4 })] });
function batchFixture(runtime = {}) {
  const f = fixture(runtime);
  f.respond = async url => jsonResponse(batchIndex(url) < 0 ? insightBody() : summaryBodies()[batchIndex(url)]);
  return f;
}

test('Facebook count summaries retain explicit zero/positive counts with exact vendor-filter labels', () => {
  const rows = helper.facebookSummaryAnalytics(facebookPostId, summaryBodies(), today, context('facebook'));
  proven(rows); assert.deepEqual(byKey(rows), { likes: '0', comments: '2', shares: '3' });
  assert.deepEqual(rows.map(row => row.label), ['Like reactions (Facebook LIKE filter)', 'Comments (Facebook stream filter)', 'Shares']);
  const bodies = summaryBodies(); bodies[2] = { id: facebookPostId };
  assert.deepEqual(byKey(helper.facebookSummaryAnalytics(facebookPostId, bodies, today, context('facebook'))), { likes: '0', comments: '2' });
  bodies[0].reactions.summary.total_count = '7'; bodies[1].comments.summary.total_count = 0;
  assert.deepEqual(byKey(helper.facebookSummaryAnalytics(facebookPostId, bodies, today, context('facebook'))), { likes: '7', comments: '0' });
});
test('Facebook summary identity, projection, nested errors and actor-bearing fields fail independently closed', () => {
  for (const index of [0, 1, 2]) {
    const edge = ['reactions', 'comments', 'shares'][index];
    for (const mutate of [
      body => { body.id = '123_999'; }, body => { delete body.id; },
      body => { body.actor = { name: sentinel }; }, body => { body[edge].actor = sentinel; },
      ...[{ error: sentinel }, { errors: [] }, { serviceErrorCode: 0 }, { status: 403 }].flatMap(marker => [
        body => Object.assign(body, marker), body => Object.assign(body[edge], marker),
        body => Object.assign(index === 2 ? body[edge] : body[edge].summary, marker),
      ]),
    ]) {
      const bodies = summaryBodies(); mutate(bodies[index]);
      const rows = helper.facebookSummaryAnalytics(facebookPostId, bodies, today, context('facebook'));
      assert.equal(rows.some(row => row.metricKey === ['likes', 'comments', 'shares'][index]), false);
      assert.equal(rows.length, 2); assert.equal(JSON.stringify(rows).includes(sentinel), false);
    }
  }
  for (const edge of ['reactions', 'comments']) for (const bad of [undefined, null, {}, [{ get name() { assert.fail('Never inspect actor content'); } }]]) {
    const bodies = summaryBodies(), index = edge === 'reactions' ? 0 : 1; bodies[index][edge].data = bad;
    assert.equal(helper.facebookSummaryAnalytics(facebookPostId, bodies, today, context('facebook')).length, 2);
  }
});
test('Facebook summary counts require their own explicit safe value and never infer missing/unsafe values', () => {
  for (const index of [0, 1, 2]) for (const value of [undefined, null, false, '', -1, 0.5, NaN, Infinity, 9007199254740992, '01', '1e3']) {
    const bodies = summaryBodies();
    if (index === 2) bodies[index].shares.count = value;
    else bodies[index][index === 0 ? 'reactions' : 'comments'].summary.total_count = value;
    assert.equal(helper.facebookSummaryAnalytics(facebookPostId, bodies, today, context('facebook')).length, 2);
  }
});
test('actual Facebook provider dispatches exactly four parallel fixed-origin GETs with header-only credentials', async () => {
  const timers = [], cleared = [], f = batchFixture({ setTimeout: (fn, ms) => { timers.push({ fn, ms }); return 1; }, clearTimeout: id => cleared.push(id) });
  const rows = await new (f.load('facebook').FacebookProvider)().postAnalytics('123', sentinel, facebookPostId, 30, context('facebook'));
  proven(rows); assert.deepEqual(byKey(rows), { reach: '0', reactions: '4', likes: '0', comments: '2', shares: '3' });
  assert.equal(f.calls.length, 4); assert.deepEqual(timers.map(t => t.ms), [15000]); assert.deepEqual(cleared, [1]);
  const expectedFields = ['id,reactions.type(LIKE).limit(0).summary(total_count)', 'id,comments.filter(stream).limit(0).summary(total_count)', 'id,shares'];
  f.calls.forEach(({ url, init }, index) => {
    const parsed = new URL(url); assert.equal(parsed.origin, 'https://graph.facebook.com');
    assert.equal(parsed.pathname, '/v25.0/' + facebookPostId + (index === 0 ? '/insights' : ''));
    assert.deepEqual([...parsed.searchParams.keys()], [index === 0 ? 'metric' : 'fields']);
    if (index) assert.equal(parsed.searchParams.get('fields'), expectedFields[index - 1]);
    assert.equal(init.method, 'GET'); assert.equal(init.body, undefined); assert.equal(init.redirect, 'error');
    assert.deepEqual(init.headers, { Authorization: 'Bearer ' + sentinel }); assert.equal(init.signal, f.calls[0].init.signal);
    assert.equal(url.includes(sentinel), false); assert.equal(url.includes('access_token'), false);
  });
  assert.deepEqual(f.logs, []);
});
test('unknown/wrong formats and nonnumeric Page-post IDs issue no additional summary calls', async () => {
  for (const postId of ['', '123', '0_456', '123_0', '123_456/actors', '123_456?fields=name', 'a_b', '1'.repeat(41) + '_2']) {
    const f = batchFixture(); assert.equal(f.load('helper').facebookCountContext(postId, context('facebook')), false);
    assert.deepEqual(await f.load('helper').facebookCountAnalytics(sentinel, postId, today, context('facebook')), []); assert.equal(f.calls.length, 0);
  }
  for (const ctx of [undefined, { provider: 'facebook', mediaKind: 'video' }, context('instagram')]) {
    const f = batchFixture();
    const rows = await new (f.load('facebook').FacebookProvider)().postAnalytics('123', sentinel, facebookPostId, 30, ctx);
    assert.equal(f.calls.length, 1); assert.ok(f.calls[0].url.includes('/insights?metric='));
    assert.ok(rows.every(row => row.provenance === undefined));
  }
});
test('failed insights retain healthy summaries and failed summaries retain healthy insights without retries', async () => {
  for (const failed of [-1, 0, 1, 2]) {
    const f = batchFixture();
    f.respond = async url => { if (batchIndex(url) === failed) throw new Error(sentinel); return jsonResponse(batchIndex(url) < 0 ? insightBody() : summaryBodies()[batchIndex(url)]); };
    const rows = await f.load('helper').facebookCountAnalytics(sentinel, facebookPostId, today, context('facebook'));
    const keys = Object.keys(byKey(rows)); assert.equal(keys.includes(failed < 0 ? 'reach' : ['likes', 'comments', 'shares'][failed]), false);
    assert.ok(keys.includes(failed < 0 ? 'likes' : 'reach')); assert.equal(f.calls.length, 4); assert.deepEqual(f.logs, []);
  }
});
test('Facebook summary transport rejects HTTP/redirect/length/JSON/UTF8 errors without inspecting actor/error bodies', async () => {
  for (const failure of ['http', 'redirect', 'declared', 'json', 'utf8']) {
    let cancelled = 0; const f = batchFixture();
    f.respond = async url => {
      if (batchIndex(url) !== 0) return jsonResponse(batchIndex(url) < 0 ? insightBody() : summaryBodies()[batchIndex(url)]);
      if (failure === 'json') return new Response('{invalid');
      if (failure === 'utf8') return new Response(new Uint8Array([255]));
      return { ok: failure !== 'http', redirected: failure === 'redirect', headers: new Headers(failure === 'declared' ? { 'content-length': '16385' } : {}),
        body: { cancel: async () => { cancelled++; }, getReader: () => assert.fail('Refused response must not read content') } };
    };
    const rows = await f.load('helper').facebookCountAnalytics(sentinel, facebookPostId, today, context('facebook'));
    assert.equal(byKey(rows).likes, undefined); assert.equal(byKey(rows).comments, '2'); assert.equal(f.calls.length, 4);
    if (!['json', 'utf8'].includes(failure)) assert.equal(cancelled, 1); assert.deepEqual(f.logs, []);
  }
});
test('decoded byte cap accepts exact16KiB and cancels an oversized stream before its next chunk', async () => {
  for (const oversized of [false, true]) {
    let reads = 0, cancelled = 0; const f = batchFixture();
    f.respond = async url => {
      if (batchIndex(url) !== 0) return jsonResponse(batchIndex(url) < 0 ? insightBody() : summaryBodies()[batchIndex(url)]);
      if (!oversized) return new Response(JSON.stringify(summaryBodies()[0]).padEnd(16384, ' '));
      return { ok: true, redirected: false, headers: new Headers(), body: { getReader: () => ({
        read: async () => { reads++; return { done: false, value: new Uint8Array(16385) }; },
        cancel: async () => { cancelled++; }, releaseLock() {},
      }) } };
    };
    const rows = await f.load('helper').facebookCountAnalytics(sentinel, facebookPostId, today, context('facebook'));
    assert.equal(byKey(rows).likes, oversized ? undefined : '0');
    if (oversized) { assert.equal(reads, 1); assert.equal(cancelled, 1); }
  }
});
test('one15s batch deadline returns healthy independent summaries even when an insights fetch never settles', async () => {
  const timers = [], f = batchFixture({ setTimeout: (fn, ms) => { timers.push({ fn, ms }); return 1; }, clearTimeout() {} });
  f.respond = async url => batchIndex(url) < 0 ? new Promise(() => {}) : jsonResponse(summaryBodies()[batchIndex(url)]);
  const pending = f.load('helper').facebookCountAnalytics(sentinel, facebookPostId, today, context('facebook'));
  assert.equal(f.calls.length, 4); await new Promise(setImmediate); timers[0].fn();
  const rows = await pending; assert.deepEqual(byKey(rows), { likes: '0', comments: '2', shares: '3' });
  assert.ok(f.calls.every(call => call.init.signal.aborted)); assert.equal(f.calls.length, 4); assert.deepEqual(f.logs, []);
});
