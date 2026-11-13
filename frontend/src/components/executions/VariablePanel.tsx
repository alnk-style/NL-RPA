import { redactSensitiveText } from '../../lib/display';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '../ui/Card';

export function VariablePanel({
  variables,
}: {
  variables: Record<string, unknown>;
}) {
  if (!Object.keys(variables).length) {
    return (
      <Card>
        <CardHeader className="border-b border-slate-100 dark:border-slate-800">
          <CardTitle>执行变量</CardTitle>
        </CardHeader>
        <CardContent className="py-5">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            该执行没有可用的变量摘要。历史执行可能未记录
            metrics.variables，本页不会推断或补造变量。
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader className="border-b border-slate-100">
        <CardTitle>执行变量</CardTitle>
      </CardHeader>
      <CardContent>
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-md bg-slate-950 p-3 font-mono text-xs text-slate-200">
          {redactSensitiveText(JSON.stringify(variables, null, 2))}
        </pre>
      </CardContent>
    </Card>
  );
}

export default VariablePanel;
