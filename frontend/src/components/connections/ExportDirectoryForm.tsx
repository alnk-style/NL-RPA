import { useEffect, useState } from 'react';
import { FolderCheck, LoaderCircle, Save } from 'lucide-react';

import { parseExportDirectoryLines } from '../../lib/connections';
import { Button } from '../ui/Button';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '../ui/Card';

export function ExportDirectoryForm({
  directories,
  saving,
  onSave,
}: {
  directories: string[];
  saving: boolean;
  onSave: (directories: string[]) => Promise<void>;
}) {
  const [value, setValue] = useState(directories.join('\n'));
  const [validation, setValidation] = useState<string | null>(null);

  useEffect(() => {
    setValue(directories.join('\n'));
  }, [directories]);

  async function submit() {
    const parsed = parseExportDirectoryLines(value);
    if (!parsed.length) {
      setValidation('请填写至少一个绝对目录路径。');
      return;
    }
    setValidation(null);
    await onSave(parsed);
  }

  return (
    <Card>
      <CardHeader className="border-b border-slate-100">
        <CardTitle className="flex items-center gap-2">
          <FolderCheck className="h-4 w-4 text-brand-600" />
          文件导出目录
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <label className="block space-y-1.5">
          <span className="text-xs font-medium">允许写入的绝对路径</span>
          <textarea
            className="min-h-28 w-full rounded-md border border-slate-200 bg-white p-3 font-mono text-xs outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-900"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder={'C:/RPA/Exports\nD:/Reports'}
          />
        </label>
        <p className="text-xs text-slate-500">
          每行一个目录，也可使用分号分隔。保存前会验证目录存在且可写。
        </p>
        {validation ? (
          <p className="text-xs text-red-600">{validation}</p>
        ) : null}
        <Button
          size="sm"
          leftIcon={saving
            ? <LoaderCircle className="h-4 w-4 animate-spin" />
            : <Save className="h-4 w-4" />}
          disabled={saving}
          onClick={() => void submit()}
        >
          保存导出目录
        </Button>
      </CardContent>
    </Card>
  );
}

export default ExportDirectoryForm;
