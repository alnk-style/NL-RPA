import { Bot, Boxes, Code2 } from 'lucide-react';
import { Link } from 'react-router-dom';

const tools = [
  {
    to: '/advanced/chat',
    title: '工作流助手',
    description: '自然语言草稿与修改',
    icon: Bot,
  },
  {
    to: '/apps',
    title: '应用',
    description: '本地应用与 MCP 工具',
    icon: Boxes,
  },
];

export function AdvancedToolsPage() {
  return (
    <section className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold">高级工具</h1>
        <p className="mt-1 text-sm text-slate-500">
          旧工作流助手、应用与调试入口
        </p>
      </header>
      <div className="grid gap-3 md:grid-cols-2">
        {tools.map((tool) => {
          const Icon = tool.icon;
          return (
            <Link
              key={tool.to}
              to={tool.to}
              className="rounded-lg border border-slate-200 bg-white p-4 hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900"
            >
              <Icon className="h-5 w-5 text-brand-600" />
              <p className="mt-3 text-sm font-semibold">{tool.title}</p>
              <p className="mt-1 text-xs text-slate-500">{tool.description}</p>
            </Link>
          );
        })}
        <div className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
          <Code2 className="h-5 w-5 text-slate-500" />
          <p className="mt-3 text-sm font-semibold">代码与原始 JSON</p>
          <p className="mt-1 text-xs text-slate-500">
            仅在 Workflow Studio 的高级区域编辑
          </p>
        </div>
      </div>
    </section>
  );
}

export default AdvancedToolsPage;
