import { useEffect, useState } from 'react';
import {
  CheckCircle2,
  LoaderCircle,
  MailCheck,
  Save,
  Send,
  XCircle,
} from 'lucide-react';

import type { EmailSettingsFormValue } from '../../lib/connections';
import type {
  EmailDeliveryResult,
  EmailSettingsRecord,
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

function toForm(settings: EmailSettingsRecord): EmailSettingsFormValue {
  return {
    host: settings.host,
    port: settings.port,
    username: settings.username,
    password: '',
    from_email: settings.from_email,
    start_tls: settings.start_tls,
    default_recipient: settings.default_recipient,
    credential_key: settings.credential_key,
  };
}

export function SmtpSettingsForm({
  settings,
  saving,
  testing,
  sendingTest,
  onSave,
  onTest,
  onSendTest,
}: {
  settings: EmailSettingsRecord;
  saving: boolean;
  testing: boolean;
  sendingTest: boolean;
  onSave: (form: EmailSettingsFormValue) => Promise<void>;
  onTest: () => Promise<EmailDeliveryResult>;
  onSendTest: (recipient: string) => Promise<EmailDeliveryResult>;
}) {
  const [form, setForm] = useState(() => toForm(settings));
  const [result, setResult] = useState<EmailDeliveryResult | null>(null);

  useEffect(() => {
    setForm(toForm(settings));
  }, [settings]);

  function patch(patchValue: Partial<EmailSettingsFormValue>) {
    setForm((current) => ({ ...current, ...patchValue }));
  }

  async function runTest(send: boolean) {
    const outcome = send
      ? await onSendTest(form.default_recipient)
      : await onTest();
    setResult(outcome);
  }

  return (
    <Card>
      <CardHeader className="border-b border-slate-100">
        <CardTitle className="flex items-center gap-2">
          <MailCheck className="h-4 w-4 text-brand-600" />
          SMTP 邮件
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Input
            label="SMTP 主机"
            value={form.host}
            onChange={(event) => patch({ host: event.target.value })}
          />
          <Input
            label="端口"
            type="number"
            value={form.port}
            onChange={(event) => patch({
              port: Number(event.target.value) || 0,
            })}
          />
          <Input
            label="用户名"
            value={form.username}
            onChange={(event) => patch({ username: event.target.value })}
          />
          <Input
            label="密码"
            type="password"
            value={form.password}
            onChange={(event) => patch({ password: event.target.value })}
            placeholder={settings.password_configured
              ? '已配置，留空保持不变'
              : '未配置'}
            hint="密码只在保存时提交，接口不返回明文。"
          />
          <Input
            label="发件人"
            type="email"
            value={form.from_email}
            onChange={(event) => patch({ from_email: event.target.value })}
          />
          <Input
            label="默认收件人"
            type="email"
            value={form.default_recipient}
            onChange={(event) => patch({
              default_recipient: event.target.value,
            })}
          />
          <Input
            label="凭据键"
            value={form.credential_key}
            onChange={(event) => patch({
              credential_key: event.target.value,
            })}
          />
          <div className="flex items-center gap-3 pt-6">
            <Switch
              checked={form.start_tls}
              onCheckedChange={(checked) => patch({ start_tls: checked })}
              label="启用 STARTTLS"
            />
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            leftIcon={saving
              ? <LoaderCircle className="h-4 w-4 animate-spin" />
              : <Save className="h-4 w-4" />}
            disabled={saving}
            onClick={() => void onSave(form)}
          >
            保存 SMTP
          </Button>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={testing
              ? <LoaderCircle className="h-4 w-4 animate-spin" />
              : <MailCheck className="h-4 w-4" />}
            disabled={testing}
            onClick={() => void runTest(false)}
          >
            测试连接
          </Button>
          <Button
            variant="secondary"
            size="sm"
            leftIcon={sendingTest
              ? <LoaderCircle className="h-4 w-4 animate-spin" />
              : <Send className="h-4 w-4" />}
            disabled={sendingTest || !form.default_recipient.trim()}
            onClick={() => void runTest(true)}
          >
            发送测试邮件
          </Button>
        </div>

        {result ? (
          <div className={`flex items-start gap-2 rounded-md border px-3 py-2 text-xs ${
            result.success
              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
              : 'border-red-200 bg-red-50 text-red-700'
          }`}>
            {result.success ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
            ) : (
              <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
            )}
            <span>
              {result.success
                ? `投递成功${result.message_id ? ` · ${result.message_id}` : ''}`
                : result.error ?? result.message ?? '投递失败'}
            </span>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export default SmtpSettingsForm;
