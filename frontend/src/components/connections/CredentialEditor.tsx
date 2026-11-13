import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  KeyRound,
  LoaderCircle,
  Plus,
  Trash2,
} from 'lucide-react';

import {
  deleteCredential,
  listCredentialKeys,
  saveCredential,
} from '../../api/connections';
import { queryKeys } from '../../api/queryKeys';
import { listWorkflows } from '../../api/workflows';
import { sanitizeCredentialKeys } from '../../lib/connections';
import { getApiErrorMessage } from '../../lib/errors';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '../ui/Card';
import { Input } from '../ui/Input';

export function CredentialEditor() {
  const queryClient = useQueryClient();
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<number | null>(
    null,
  );
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const workflowsQuery = useQuery({
    queryKey: queryKeys.workflows,
    queryFn: listWorkflows,
  });
  const workflows = workflowsQuery.data ?? [];
  const workflowId = selectedWorkflowId ?? workflows[0]?.id ?? null;
  const credentialsQuery = useQuery({
    queryKey: queryKeys.credentials(workflowId ?? 0),
    queryFn: () => listCredentialKeys(Number(workflowId)),
    enabled: workflowId !== null,
  });
  const keys = useMemo(
    () => sanitizeCredentialKeys(credentialsQuery.data?.keys),
    [credentialsQuery.data?.keys],
  );
  const saveMutation = useMutation({
    mutationFn: ({
      targetWorkflowId,
      credentialKey,
      credentialValue,
    }: {
      targetWorkflowId: number;
      credentialKey: string;
      credentialValue: string;
    }) => saveCredential(
      targetWorkflowId,
      credentialKey,
      credentialValue,
    ),
  });
  const deleteMutation = useMutation({
    mutationFn: ({
      targetWorkflowId,
      credentialKey,
    }: {
      targetWorkflowId: number;
      credentialKey: string;
    }) => deleteCredential(targetWorkflowId, credentialKey),
  });

  async function submit() {
    if (!workflowId || !/^[A-Za-z0-9_.-]{1,255}$/.test(key) || !value) {
      setError('凭据键只能使用字母、数字、点、横线和下划线。');
      return;
    }
    setError(null);
    try {
      await saveMutation.mutateAsync({
        targetWorkflowId: workflowId,
        credentialKey: key,
        credentialValue: value,
      });
      setKey('');
      setValue('');
      await queryClient.invalidateQueries({
        queryKey: queryKeys.credentials(workflowId),
      });
    } catch (saveError) {
      setError(getApiErrorMessage(saveError, '保存凭据失败'));
    }
  }

  async function remove(targetKey: string) {
    if (!workflowId) {
      return;
    }
    setError(null);
    try {
      await deleteMutation.mutateAsync({
        targetWorkflowId: workflowId,
        credentialKey: targetKey,
      });
      setDeleteTarget(null);
      await queryClient.invalidateQueries({
        queryKey: queryKeys.credentials(workflowId),
      });
    } catch (deleteError) {
      setError(getApiErrorMessage(deleteError, '删除凭据失败'));
    }
  }

  return (
    <Card>
      <CardHeader className="border-b border-slate-100">
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-brand-600" />
          工作流凭据
        </CardTitle>
        <p className="mt-1 text-xs text-slate-500">
          凭据值加密保存，页面只显示键名。
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <label className="block space-y-1.5">
          <span className="text-xs font-medium">工作流</span>
          <select
            className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-900"
            value={workflowId ?? ''}
            onChange={(event) => {
              setSelectedWorkflowId(Number(event.target.value) || null);
              setDeleteTarget(null);
              setError(null);
            }}
          >
            {workflows.map((workflow) => (
              <option key={workflow.id} value={workflow.id}>
                {workflow.name}
              </option>
            ))}
          </select>
        </label>

        {keys.length ? (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {keys.map((credentialKey) => (
              <li
                key={credentialKey}
                className="flex flex-wrap items-center gap-2 py-2 text-sm"
              >
                <span className="font-mono">{credentialKey}</span>
                <Badge variant="secondary">已加密</Badge>
                {deleteTarget === credentialKey ? (
                  <div className="ml-auto flex items-center gap-2">
                    <span className="text-xs text-amber-700">确认删除？</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setDeleteTarget(null)}
                    >
                      取消
                    </Button>
                    <Button
                      variant="danger"
                      size="sm"
                      disabled={deleteMutation.isPending}
                      onClick={() => void remove(credentialKey)}
                    >
                      删除
                    </Button>
                  </div>
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto"
                    aria-label={`删除凭据 ${credentialKey}`}
                    leftIcon={<Trash2 className="h-3.5 w-3.5" />}
                    onClick={() => setDeleteTarget(credentialKey)}
                  />
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">当前工作流尚无凭据。</p>
        )}

        <div className="grid gap-3 border-t border-slate-100 pt-4 sm:grid-cols-2 dark:border-slate-800">
          <Input
            label="凭据键"
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder="例如 smtp_default"
          />
          <Input
            label="凭据值"
            type="password"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="保存后不再回显"
          />
          <div className="sm:col-span-2">
            <Button
              size="sm"
              leftIcon={saveMutation.isPending
                ? <LoaderCircle className="h-4 w-4 animate-spin" />
                : <Plus className="h-4 w-4" />}
              disabled={saveMutation.isPending || !workflowId}
              onClick={() => void submit()}
            >
              保存凭据
            </Button>
          </div>
        </div>
        {error ? <p className="text-xs text-red-600">{error}</p> : null}
      </CardContent>
    </Card>
  );
}

export default CredentialEditor;
