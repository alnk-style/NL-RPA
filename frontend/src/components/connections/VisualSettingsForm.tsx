import { useEffect, useState } from 'react';
import {
  Eye,
  LoaderCircle,
  Save,
  ShieldAlert,
  TestTube2,
} from 'lucide-react';

import type {
  VisualProviderName,
  VisualProviderTestResult,
  VisualSettingsRecord,
} from '../../types/connections';
import { Button } from '../ui/Button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '../ui/Card';
import { Input } from '../ui/Input';
import { Switch } from '../ui/Switch';

export function VisualSettingsForm({
  settings,
  saving,
  testing,
  onSave,
  onTest,
}: {
  settings: VisualSettingsRecord;
  saving: boolean;
  testing: boolean;
  onSave: (payload: {
    enabled: boolean;
    provider: VisualProviderName;
    endpoint: string;
    model: string;
    confidence_threshold: number;
    max_candidates: number;
    max_attempts: number;
    screenshot_scope: 'viewport' | 'window';
    redaction_policy: Record<string, unknown>;
    api_key?: string | null;
  }) => Promise<void>;
  onTest: () => Promise<VisualProviderTestResult>;
}) {
  const [enabled, setEnabled] = useState(settings.enabled);
  const [provider, setProvider] = useState<VisualProviderName>(
    settings.provider,
  );
  const [endpoint, setEndpoint] = useState(settings.endpoint);
  const [model, setModel] = useState(settings.model);
  const [threshold, setThreshold] = useState(
    String(settings.confidence_threshold),
  );
  const [maxCandidates, setMaxCandidates] = useState(
    String(settings.max_candidates),
  );
  const [maxAttempts, setMaxAttempts] = useState(
    String(settings.max_attempts),
  );
  const [scope, setScope] = useState<'viewport' | 'window'>(
    settings.screenshot_scope,
  );
  const [apiKey, setApiKey] = useState('');
  const [testResult, setTestResult] =
    useState<VisualProviderTestResult | null>(null);

  useEffect(() => {
    setEnabled(settings.enabled);
    setProvider(settings.provider);
    setEndpoint(settings.endpoint);
    setModel(settings.model);
    setThreshold(String(settings.confidence_threshold));
    setMaxCandidates(String(settings.max_candidates));
    setMaxAttempts(String(settings.max_attempts));
    setScope(settings.screenshot_scope);
  }, [settings]);

  async function submit() {
    await onSave({
      enabled,
      provider,
      endpoint: endpoint.trim(),
      model: model.trim(),
      confidence_threshold: Number(threshold),
      max_candidates: Number(maxCandidates),
      max_attempts: Number(maxAttempts),
      screenshot_scope: scope,
      redaction_policy: settings.redaction_policy,
      ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
    });
    setApiKey('');
  }

  async function runTest() {
    setTestResult(await onTest());
  }

  return (
    <Card>
      <CardHeader className="border-b border-slate-100 dark:border-slate-800">
        <CardTitle className="flex items-center gap-2">
          <Eye className="h-4 w-4 text-brand-600" />
          视觉兜底
        </CardTitle>
        <p className="mt-1 text-xs text-slate-500">
          视觉仅处理结构化定位失败；默认关闭，候选必须经服务端校验。
        </p>
      </CardHeader>
      <CardContent className="space-y-5 pt-5">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 px-3 py-3 dark:border-slate-700">
          <div>
            <p className="text-sm font-medium">启用视觉兜底</p>
            <p className="mt-0.5 text-xs text-slate-500">
              步骤仍需显式设置 visual_fallback=true。
            </p>
          </div>
          <Switch
            checked={enabled}
            onCheckedChange={setEnabled}
            label="启用视觉兜底"
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-xs font-medium text-slate-600 dark:text-slate-300">
            Provider
            <select
              value={provider}
              onChange={(event) => {
                const value = event.target.value as VisualProviderName;
                setProvider(value);
                if (value === 'disabled') {
                  setEnabled(false);
                }
              }}
              className="h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-900"
            >
              <option value="disabled">disabled</option>
              <option value="local_ocr">local_ocr</option>
              <option value="remote_vlm">remote_vlm</option>
            </select>
          </label>
          <label className="space-y-1 text-xs font-medium text-slate-600 dark:text-slate-300">
            截图范围
            <select
              value={scope}
              onChange={(event) => {
                setScope(
                  event.target.value === 'window'
                    ? 'window'
                    : 'viewport',
                );
              }}
              className="h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-900"
            >
              <option value="viewport">页面视口 / 目标窗口</option>
              <option value="window">目标窗口</option>
            </select>
          </label>
        </div>

        {provider === 'remote_vlm' ? (
          <>
            <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
              远程视觉会把脱敏后的目标区域截图发送到配置的 HTTPS 端点。
              密码、Token、Cookie、银行卡和路径会在发送前遮罩。
            </div>
            <Input
              label="HTTPS Endpoint"
              value={endpoint}
              onChange={(event) => setEndpoint(event.target.value)}
              placeholder="https://visual.example.com/locate"
            />
            <Input
              label="模型"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="视觉模型名称"
            />
            <Input
              label={
                settings.api_key_configured
                  ? 'API 密钥（已配置，留空不修改）'
                  : 'API 密钥'
              }
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={
                settings.api_key_configured ? '已配置' : '输入密钥'
              }
            />
          </>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-3">
          <Input
            label="置信度阈值"
            type="number"
            min="0"
            max="1"
            step="0.01"
            value={threshold}
            onChange={(event) => setThreshold(event.target.value)}
          />
          <Input
            label="最大候选数"
            type="number"
            min="1"
            max="3"
            value={maxCandidates}
            onChange={(event) => setMaxCandidates(event.target.value)}
          />
          <Input
            label="最大尝试次数"
            type="number"
            min="1"
            max="10"
            value={maxAttempts}
            onChange={(event) => setMaxAttempts(event.target.value)}
          />
        </div>

        {testResult ? (
          <p
            className={
              testResult.status === 'ok'
                ? 'rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-200'
                : 'rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200'
            }
          >
            {testResult.status}
            {testResult.model ? ` · ${testResult.model}` : ''}
            {typeof testResult.candidate_count === 'number'
              ? ` · ${testResult.candidate_count} 个候选`
              : ''}
            {testResult.message ? ` · ${testResult.message}` : ''}
          </p>
        ) : null}

        <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 pt-4 dark:border-slate-800">
          <Button
            variant="secondary"
            size="sm"
            leftIcon={
              testing
                ? <LoaderCircle className="h-4 w-4 animate-spin" />
                : <TestTube2 className="h-4 w-4" />
            }
            disabled={testing || saving}
            onClick={() => void runTest()}
          >
            安全自检
          </Button>
          <Button
            size="sm"
            leftIcon={
              saving
                ? <LoaderCircle className="h-4 w-4 animate-spin" />
                : <Save className="h-4 w-4" />
            }
            disabled={saving || testing}
            onClick={() => void submit()}
          >
            保存视觉设置
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default VisualSettingsForm;
