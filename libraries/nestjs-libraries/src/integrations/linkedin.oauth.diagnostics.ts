import { AsyncLocalStorage } from 'node:async_hooks';
import { randomUUID } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import { HttpException } from '@nestjs/common';
import {
  isOAuthDiagnosticId,
  OAuthDiagnostic,
  OAuthDiagnosticError,
  OAuthDiagnosticStage,
  safeOAuthDiagnostic,
} from '@gitroom/helpers/utils/oauth.diagnostics';

interface DiagnosticStore {
  get(key: string): Promise<string | null>;
  set(key: string, value: string, mode: 'EX', seconds: number): Promise<unknown>;
}
interface Context {
  correlationId: string;
  failure?: OAuthDiagnostic;
}

// Only LinkedIn providers opt in through the optional provider interface.
export class LinkedInOAuthDiagnostics {
  private readonly context = new AsyncLocalStorage<Context>();

  constructor(private readonly provider: 'linkedin' | 'linkedin-page') {}

  dashboardStage(name: string): OAuthDiagnosticStage | undefined {
    return this.provider === 'linkedin-page' && name === 'companies' ? 'page_list' : undefined;
  }

  private enabled() {
    return process.env.LINKEDIN_OAUTH_DIAGNOSTICS === 'true';
  }

  private async storageOperation<T>(operation: Promise<T>): Promise<T> {
    const configured = Number(process.env.LINKEDIN_OAUTH_DIAGNOSTICS_STORE_TIMEOUT_MS);
    const timeout = Number.isSafeInteger(configured) && configured > 0 ? configured : 250;
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await Promise.race([
        operation,
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error('Diagnostic storage unavailable')), timeout);
        }),
      ]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  private emit(
    stage: OAuthDiagnosticStage,
    outcome: OAuthDiagnostic['outcome'],
    extra: Pick<OAuthDiagnostic, 'error' | 'providerCode' | 'status' | 'durationMs'> = {},
    functionalFailure = true
  ) {
    const context = this.context.getStore();
    if (!this.enabled() || !context) return;
    const diagnostic = safeOAuthDiagnostic({
      event: 'oauth_diagnostic', provider: this.provider,
      correlationId: context.correlationId, stage, outcome, ...extra,
    });
    if (!diagnostic) return;
    if (outcome === 'failure' && functionalFailure) context.failure ??= diagnostic;
    try {
      console.info(JSON.stringify(diagnostic));
    } catch {}
    return diagnostic;
  }

  failure(stage: OAuthDiagnosticStage, error: OAuthDiagnosticError = 'operation_error') {
    this.emit(stage, 'failure', { error });
  }

  // OAuth state / integration IDs are private lookup keys only, never log fields.
  async remember(store: DiagnosticStore, key: string) {
    const context = this.context.getStore();
    if (!this.enabled() || !context) return;
    const configured = Number(process.env.LINKEDIN_OAUTH_DIAGNOSTICS_TTL_SECONDS);
    const ttl = Number.isSafeInteger(configured) && configured > 0 ? configured : 3600;
    try {
      await this.storageOperation(store.set(`oauth-diagnostic:${this.provider}:${key}`, context.correlationId, 'EX', ttl));
    } catch {
      this.emit('connect', 'failure', { error: 'correlation_storage_error' }, false);
    }
  }

  async rememberPageAttempt(store: DiagnosticStore, integrationId: string) {
    const context = this.context.getStore();
    if (!this.enabled() || !context) return;
    await this.remember(store, `page-attempt:${integrationId}:${context.correlationId}`);
  }

  async resumePageSave<T>(store: DiagnosticStore, integrationId: string, binding: unknown, operation: () => Promise<T>) {
    if (!isOAuthDiagnosticId(binding)) return this.run('page_save', operation);
    return this.resume(store, `page-attempt:${integrationId}:${binding}`, 'page_save', operation, binding);
  }

  async resume<T>(store: DiagnosticStore, key: string, stage: OAuthDiagnosticStage, operation: () => Promise<T>, expectedCorrelationId?: string) {
    if (!this.enabled()) return operation();
    let correlationId: string | undefined;
    let storageFailed = false;
    try {
      const stored = await this.storageOperation(store.get(`oauth-diagnostic:${this.provider}:${key}`));
      if (isOAuthDiagnosticId(stored) && (!expectedCorrelationId || stored === expectedCorrelationId)) correlationId = stored;
    } catch {
      storageFailed = true;
    }
    return this.run(stage, async () => {
      if (storageFailed) this.emit(stage, 'failure', { error: 'correlation_storage_error' }, false);
      return operation();
    }, correlationId);
  }

  async run<T>(stage: OAuthDiagnosticStage, operation: () => Promise<T>, correlationId?: string): Promise<T> {
    if (!this.enabled()) return operation();
    return this.context.run({ correlationId: isOAuthDiagnosticId(correlationId) ? correlationId : randomUUID() }, async () => {
      const started = performance.now();
      this.emit(stage, 'started');
      try {
        const result = await operation();
        const failure = this.context.getStore()?.failure;
        const diagnostic = failure || this.emit(stage, 'success', { durationMs: Math.round(performance.now() - started) });
        // Add only safe metadata, keeping successful provider payloads intact.
        return result && typeof result === 'object' && !Array.isArray(result)
          ? { ...result, diagnostics: diagnostic } : result;
      } catch (error) {
        const diagnostic = this.context.getStore()?.failure ||
          this.emit(stage, 'failure', { error: 'operation_error', durationMs: Math.round(performance.now() - started) });
        // No original error, stack, cause or provider response crosses this boundary.
        throw new HttpException({
          message: `LinkedIn connection failed at ${diagnostic!.stage}. Reference: ${diagnostic!.correlationId}`,
          diagnostics: diagnostic,
        }, error instanceof HttpException ? error.getStatus() : 409);
      }
    });
  }

  async step<T>(stage: OAuthDiagnosticStage, operation: () => Promise<T>): Promise<T> {
    if (!this.enabled() || !this.context.getStore()) return operation();
    const started = performance.now();
    this.emit(stage, 'started');
    try {
      const result = await operation();
      this.emit(stage, 'success', { durationMs: Math.round(performance.now() - started) });
      return result;
    } catch (error) {
      this.emit(stage, 'failure', {
        error: stage === 'scope_check' ? 'scope_error' : 'operation_error',
        durationMs: Math.round(performance.now() - started),
      });
      throw error;
    }
  }

  async requestJson(stage: OAuthDiagnosticStage, send: () => Promise<Response>): Promise<any> {
    if (!this.enabled() || !this.context.getStore()) return (await send()).json();
    const started = performance.now();
    this.emit(stage, 'started');
    let response: Response;
    try {
      response = await send();
    } catch (error) {
      this.emit(stage, 'failure', { error: 'network_error', durationMs: Math.round(performance.now() - started) });
      throw error;
    }
    try {
      const body = await response.json();
      const oauthError = stage === 'token_exchange' && typeof body?.error === 'string';
      this.emit(stage, response.ok && !oauthError ? 'success' : 'failure', {
        status: response.status, durationMs: Math.round(performance.now() - started),
        ...(!response.ok ? { error: 'http_error' as const } : oauthError ? { error: 'oauth_error' as const } : {}),
        ...(oauthError ? { providerCode: body.error } : {}),
      });
      return body;
    } catch (error) {
      this.emit(stage, 'failure', {
        status: response.status, error: 'invalid_json',
        durationMs: Math.round(performance.now() - started),
      });
      throw error;
    }
  }
}
