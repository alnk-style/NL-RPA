import { useEffect, useMemo } from 'react';
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from 'react-flow-renderer';
import { GitBranch } from 'lucide-react';

import { cn } from '../lib/utils';
import { buildWorkflowGraph } from '../lib/workflow-graph';
import type { StructuredPlan } from '../types/workflow';

interface WorkflowNodeData {
  kind: 'trigger' | 'step';
  label: string;
  detail?: string;
  stepIndex: number;
}

const nodeTypes = {
  workflow: WorkflowNode,
};

function WorkflowNode({ data }: NodeProps<WorkflowNodeData>) {
  const isTrigger = data.kind === 'trigger';

  return (
    <div
      className={cn(
        'w-[220px] rounded-md border bg-white px-3 py-2.5 shadow-sm',
        'dark:bg-slate-900',
        isTrigger
          ? 'border-brand-300 dark:border-brand-700'
          : 'border-slate-200 dark:border-slate-700',
      )}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!h-1.5 !w-1.5 !border-0 !bg-slate-400"
      />
      <div className="flex items-start gap-2.5">
        <div
          className={cn(
            'mt-1 h-2 w-2 shrink-0 rounded-full',
            isTrigger ? 'bg-brand-500' : 'bg-slate-400',
          )}
        />
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
            {isTrigger ? '触发条件' : `步骤 ${data.stepIndex + 1}`}
          </p>
          <p className="mt-1 text-xs font-semibold leading-5 text-slate-800 dark:text-slate-100">
            {data.label}
          </p>
          {data.detail ? (
            <p className="mt-1 break-all text-[11px] leading-4 text-slate-500 dark:text-slate-400">
              {data.detail}
            </p>
          ) : null}
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="!h-1.5 !w-1.5 !border-0 !bg-slate-400"
      />
    </div>
  );
}

function FlowCanvas({ plan }: { plan: StructuredPlan }) {
  const graph = useMemo(() => buildWorkflowGraph(plan), [plan]);
  const { fitView } = useReactFlow();

  const nodes = useMemo<Node<WorkflowNodeData>[]>(
    () =>
      graph.nodes.map((node, index) => ({
        id: node.id,
        type: 'workflow',
        position: {
          x: 145,
          y: index * 126,
        },
        data: {
          kind: node.kind,
          label: node.label,
          detail: node.detail,
          stepIndex: node.stepIndex,
        },
      })),
    [graph.nodes],
  );

  const edges = useMemo<Edge[]>(
    () =>
      graph.edges.map((edge) => ({
        ...edge,
        type: 'smoothstep',
        animated: true,
        style: {
          stroke: '#60a5fa',
          strokeWidth: 1.5,
        },
      })),
    [graph.edges],
  );

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      fitView({
        padding: 0.2,
        duration: 250,
      });
    });

    return () => window.cancelAnimationFrame(frame);
  }, [fitView, nodes, edges]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      defaultEdgeOptions={{
        type: 'smoothstep',
      }}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      fitView
      fitViewOptions={{ padding: 0.2 }}
      minZoom={0.5}
      maxZoom={1.4}
      attributionPosition="bottom-right"
      style={{
        width: '100%',
        height: '100%',
        minHeight: 420,
      }}
    >
      <Background color="#cbd5e1" gap={20} size={1} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

export interface WorkflowDagPreviewProps {
  plan?: StructuredPlan | null;
  className?: string;
}

export function WorkflowDagPreview({
  plan,
  className,
}: WorkflowDagPreviewProps) {
  if (!plan || !plan.steps?.length) {
    return (
      <div
        className={cn(
          'flex min-h-[320px] flex-col items-center justify-center gap-3 rounded-md border border-dashed border-slate-300 bg-slate-50 text-center dark:border-slate-700 dark:bg-slate-950/40',
          className,
        )}
      >
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-slate-200 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
          <GitBranch className="h-5 w-5" />
        </div>
        <div>
          <p className="text-sm font-medium text-slate-600 dark:text-slate-300">
            暂无流程结构
          </p>
          <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
            生成方案后将在这里展示节点和连线
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      className={cn(
        'h-full min-h-[420px] overflow-hidden rounded-md border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-950',
        className,
      )}
    >
      <ReactFlowProvider>
        <FlowCanvas plan={plan} />
      </ReactFlowProvider>
    </div>
  );
}

export default WorkflowDagPreview;
