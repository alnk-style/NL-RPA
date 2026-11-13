import { Boxes, ShieldAlert } from 'lucide-react';

import {
  mcpPlanSteps,
  type MCPPlanStepSummary,
} from '../../lib/chat-plan';
import { riskLabel, riskVariant } from '../../lib/mcp-settings';
import type {
  MCPToolCatalogItem,
} from '../../types/mcp';
import type { StructuredPlan } from '../../types/workflow';
import { Badge } from '../ui/Badge';

export function MCPPlanPreview({
  plan,
  catalog = [],
}: {
  plan: StructuredPlan | null;
  catalog?: MCPToolCatalogItem[];
}) {
  const steps = mcpPlanSteps(plan);
  if (steps.length === 0) {
    return null;
  }

  return (
    <section className="space-y-2" aria-label="MCP 工具步骤">
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-slate-700 dark:text-slate-200">
          <Boxes className="h-3.5 w-3.5 text-teal-600" />
          应用工具步骤
        </p>
        <span className="text-[11px] text-slate-400">
          {steps.length} 个
        </span>
      </div>
      <div className="divide-y divide-slate-100 overflow-hidden rounded-md border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
        {steps.map((step) => (
          <MCPPlanRow
            key={step.stepId}
            step={step}
            metadata={catalog.find(
              (item) => (
                item.server === step.server && item.tool === step.tool
              ),
            )}
          />
        ))}
      </div>
    </section>
  );
}

function MCPPlanRow({
  step,
  metadata,
}: {
  step: MCPPlanStepSummary;
  metadata?: MCPToolCatalogItem;
}) {
  const risk = step.riskLevel || metadata?.risk_level || '';
  const writeRisk = risk === 'mutating' || risk === 'destructive';

  return (
    <div className="space-y-1.5 px-3 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="break-all text-xs font-semibold text-slate-800 dark:text-slate-100">
            {step.server || '未选择应用'} · {step.tool || '未选择工具'}
          </p>
          <p className="mt-0.5 text-[11px] text-slate-400">
            步骤 {step.stepId}
          </p>
        </div>
        {risk ? (
          <Badge variant={riskVariant(risk)}>
            {riskLabel(risk)}
          </Badge>
        ) : null}
      </div>
      <p className="break-words text-xs leading-5 text-slate-500 dark:text-slate-400">
        {metadata?.description || '该工具暂无补充说明。'}
      </p>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-400">
        <span>
          {step.hasArguments
            ? `参数：${step.argumentKeys.join('、')}`
            : '无参数'}
        </span>
        {step.dependsOn.length > 0 ? (
          <span>依赖步骤 {step.dependsOn.join('、')}</span>
        ) : (
          <span>无前置依赖</span>
        )}
      </div>
      {writeRisk || step.requiresConfirmation ? (
        <div className="flex items-start gap-1.5 rounded bg-amber-50 px-2 py-1.5 text-[11px] leading-4 text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            {writeRisk ? '写入或高风险操作。' : ''}
            {step.requiresConfirmation ? '执行前需要确认。' : ''}
          </span>
        </div>
      ) : null}
    </div>
  );
}

export default MCPPlanPreview;
