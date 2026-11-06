const stages = [
  'connect', 'callback', 'token_exchange', 'scope_check', 'userinfo',
  'member_profile', 'page_list', 'page_details', 'page_save',
] as const;
const errors = [
  'http_error', 'network_error', 'invalid_json', 'scope_error',
  'empty_result', 'operation_error', 'correlation_storage_error',
  'oauth_error',
] as const;
const providerCodes = [
  'access_denied', 'invalid_request', 'invalid_client', 'invalid_grant',
  'invalid_scope', 'unauthorized_client', 'unsupported_grant_type',
  'unsupported_response_type', 'server_error', 'temporarily_unavailable',
] as const;

export type OAuthDiagnosticStage = typeof stages[number];
export type OAuthDiagnosticError = typeof errors[number];
export interface OAuthDiagnostic {
  event: 'oauth_diagnostic';
  provider: 'linkedin' | 'linkedin-page';
  correlationId: string;
  stage: OAuthDiagnosticStage;
  outcome: 'started' | 'success' | 'failure';
  error?: OAuthDiagnosticError;
  providerCode?: typeof providerCodes[number];
  status?: number;
  durationMs?: number;
}

export const isOAuthDiagnosticId = (value: unknown): value is string =>
  typeof value === 'string' &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);

// Rebuild from a closed schema. Never spread caller/provider data into a log.
export function safeOAuthDiagnostic(value: unknown): OAuthDiagnostic | undefined {
  if (!value || typeof value !== 'object') return;
  const item = value as Record<string, unknown>;
  if (
    item.event !== 'oauth_diagnostic' ||
    !['linkedin', 'linkedin-page'].includes(item.provider as string) ||
    !isOAuthDiagnosticId(item.correlationId) ||
    !stages.includes(item.stage as OAuthDiagnosticStage) ||
    !['started', 'success', 'failure'].includes(item.outcome as string)
  ) return;
  return {
    event: 'oauth_diagnostic',
    provider: item.provider as OAuthDiagnostic['provider'],
    correlationId: item.correlationId,
    stage: item.stage as OAuthDiagnosticStage,
    outcome: item.outcome as OAuthDiagnostic['outcome'],
    ...(errors.includes(item.error as OAuthDiagnosticError)
      ? { error: item.error as OAuthDiagnosticError } : {}),
    ...(providerCodes.includes(item.providerCode as typeof providerCodes[number])
      ? { providerCode: item.providerCode as typeof providerCodes[number] } : {}),
    ...(Number.isInteger(item.status) && Number(item.status) >= 100 && Number(item.status) <= 599
      ? { status: Number(item.status) } : {}),
    ...(Number.isSafeInteger(item.durationMs) && Number(item.durationMs) >= 0
      ? { durationMs: Number(item.durationMs) } : {}),
  };
}

export function reportOAuthDiagnostic(value: unknown): string | undefined {
  const diagnostic = safeOAuthDiagnostic(value);
  if (!diagnostic) return;
  try {
    console.info(JSON.stringify(diagnostic));
  } catch {}
  if (diagnostic.outcome === 'failure') {
    return `LinkedIn connection failed at ${diagnostic.stage}. Reference: ${diagnostic.correlationId}`;
  }
}
