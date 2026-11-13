import type {
  StructuredPlan,
  WorkflowStep,
} from '../types/workflow';

export interface WorkflowMutationResult {
  plan: StructuredPlan;
  error?: string;
}

export interface ActionPresentation {
  label: string;
  description: string;
  nodeClass: string;
  badgeClass: string;
  handleClass: string;
}

const ACTION_PRESENTATIONS: Record<string, ActionPresentation> = {
  navigate: {
    label: '打开页面',
    description: '导航到目标网址',
    nodeClass: 'border-sky-300 bg-sky-50',
    badgeClass: 'bg-sky-100 text-sky-700',
    handleClass: '!bg-sky-500',
  },
  goto: {
    label: '打开页面',
    description: '导航到目标网址',
    nodeClass: 'border-sky-300 bg-sky-50',
    badgeClass: 'bg-sky-100 text-sky-700',
    handleClass: '!bg-sky-500',
  },
  click: {
    label: '点击元素',
    description: '点击页面目标',
    nodeClass: 'border-violet-300 bg-violet-50',
    badgeClass: 'bg-violet-100 text-violet-700',
    handleClass: '!bg-violet-500',
  },
  fill: {
    label: '填写输入',
    description: '向输入框写入内容',
    nodeClass: 'border-amber-300 bg-amber-50',
    badgeClass: 'bg-amber-100 text-amber-700',
    handleClass: '!bg-amber-500',
  },
  extract: {
    label: '提取数据',
    description: '从页面读取数据',
    nodeClass: 'border-emerald-300 bg-emerald-50',
    badgeClass: 'bg-emerald-100 text-emerald-700',
    handleClass: '!bg-emerald-500',
  },
  compare: {
    label: '数据对比',
    description: '比较提取值与预期',
    nodeClass: 'border-orange-300 bg-orange-50',
    badgeClass: 'bg-orange-100 text-orange-700',
    handleClass: '!bg-orange-500',
  },
  notify: {
    label: '发送通知',
    description: '推送执行结果',
    nodeClass: 'border-rose-300 bg-rose-50',
    badgeClass: 'bg-rose-100 text-rose-700',
    handleClass: '!bg-rose-500',
  },
  wait: {
    label: '等待条件',
    description: '等待页面或时间条件',
    nodeClass: 'border-cyan-300 bg-cyan-50',
    badgeClass: 'bg-cyan-100 text-cyan-700',
    handleClass: '!bg-cyan-500',
  },
  press: {
    label: '键盘操作',
    description: '向页面发送按键',
    nodeClass: 'border-indigo-300 bg-indigo-50',
    badgeClass: 'bg-indigo-100 text-indigo-700',
    handleClass: '!bg-indigo-500',
  },
  screenshot: {
    label: '页面截图',
    description: '保存当前页面截图',
    nodeClass: 'border-fuchsia-300 bg-fuchsia-50',
    badgeClass: 'bg-fuchsia-100 text-fuchsia-700',
    handleClass: '!bg-fuchsia-500',
  },
  call_tool: {
    label: 'MCP 工具',
    description: '通过已授权应用工具执行操作',
    nodeClass: 'border-teal-300 bg-teal-50',
    badgeClass: 'bg-teal-100 text-teal-700',
    handleClass: '!bg-teal-500',
  },
  launch_app: {
    label: '启动程序',
    description: '启动白名单内的 Windows 程序',
    nodeClass: 'border-sky-300 bg-sky-50',
    badgeClass: 'bg-sky-100 text-sky-700',
    handleClass: '!bg-sky-500',
  },
  activate_window: {
    label: '激活窗口',
    description: '切换到目标窗口',
    nodeClass: 'border-cyan-300 bg-cyan-50',
    badgeClass: 'bg-cyan-100 text-cyan-700',
    handleClass: '!bg-cyan-500',
  },
  click_element: {
    label: '点击控件',
    description: '点击界面上的控件',
    nodeClass: 'border-violet-300 bg-violet-50',
    badgeClass: 'bg-violet-100 text-violet-700',
    handleClass: '!bg-violet-500',
  },
  type_text: {
    label: '输入文本',
    description: '向当前焦点写入内容',
    nodeClass: 'border-amber-300 bg-amber-50',
    badgeClass: 'bg-amber-100 text-amber-700',
    handleClass: '!bg-amber-500',
  },
  press_hotkey: {
    label: '发送快捷键',
    description: '向当前窗口发送快捷键',
    nodeClass: 'border-indigo-300 bg-indigo-50',
    badgeClass: 'bg-indigo-100 text-indigo-700',
    handleClass: '!bg-indigo-500',
  },
  read_text: {
    label: '读取文本',
    description: '读取界面控件中的文本',
    nodeClass: 'border-emerald-300 bg-emerald-50',
    badgeClass: 'bg-emerald-100 text-emerald-700',
    handleClass: '!bg-emerald-500',
  },
  create_document: {
    label: '新建文档',
    description: '通过 WPS 或 Office 新建文档',
    nodeClass: 'border-teal-300 bg-teal-50',
    badgeClass: 'bg-teal-100 text-teal-700',
    handleClass: '!bg-teal-500',
  },
  close_app: {
    label: '关闭程序',
    description: '关闭先前启动的程序',
    nodeClass: 'border-rose-300 bg-rose-50',
    badgeClass: 'bg-rose-100 text-rose-700',
    handleClass: '!bg-rose-500',
  },
  auto_repair: {
    label: '自动修复',
    description: '用真实控件重写失败的步骤',
    nodeClass: 'border-amber-300 bg-amber-50',
    badgeClass: 'bg-amber-100 text-amber-700',
    handleClass: '!bg-amber-500',
  },
};

const FALLBACK_PRESENTATION: ActionPresentation = {
  label: '自定义步骤',
  description: '执行自定义动作',
  nodeClass: 'border-slate-300 bg-slate-50',
  badgeClass: 'bg-slate-100 text-slate-700',
  handleClass: '!bg-slate-500',
};

export function getActionPresentation(
  action: string | undefined,
): ActionPresentation {
  return (
    ACTION_PRESENTATIONS[String(action ?? '').toLowerCase()] ??
    FALLBACK_PRESENTATION
  );
}

export function workflowStepKey(
  step: WorkflowStep,
  index: number,
): string {
  return String(step.step_id ?? index + 1);
}

export function ensureExplicitDependencies(
  plan: StructuredPlan,
): StructuredPlan {
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const normalizedSteps = steps.map((step, index) => {
    if (Array.isArray(step.depends_on)) {
      return {
        ...step,
        depends_on: [...step.depends_on],
      };
    }

    const previous = index > 0 ? steps[index - 1] : null;
    return {
      ...step,
      depends_on: previous
        ? [workflowStepKey(previous, index - 1)]
        : [],
    };
  });

  return {
    ...plan,
    steps: normalizedSteps,
  };
}

function nodeToStepIndex(
  plan: StructuredPlan,
  nodeId: string,
): number | null {
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const index = steps.findIndex(
    (step, stepIndex) => (
      `step-${workflowStepKey(step, stepIndex)}` === nodeId
    ),
  );
  return index === -1 ? null : index;
}

function dependsOnStep(
  dependencyMap: Map<string, string[]>,
  currentId: string,
  expectedId: string,
  visited = new Set<string>(),
): boolean {
  if (visited.has(currentId)) {
    return false;
  }
  visited.add(currentId);

  const dependencies = dependencyMap.get(currentId) ?? [];
  for (const dependency of dependencies) {
    if (dependency === expectedId) {
      return true;
    }
    if (dependsOnStep(dependencyMap, dependency, expectedId, visited)) {
      return true;
    }
  }
  return false;
}

export function connectWorkflowNodes(
  plan: StructuredPlan,
  sourceNodeId: string,
  targetNodeId: string,
): WorkflowMutationResult {
  const normalizedPlan = ensureExplicitDependencies(plan);
  const steps = normalizedPlan.steps ?? [];
  const targetIndex = nodeToStepIndex(normalizedPlan, targetNodeId);

  if (targetIndex === null) {
    return { plan, error: '只有步骤节点可以建立依赖关系。' };
  }
  if (sourceNodeId === 'trigger') {
    const nextSteps = steps.map((step, index) => (
      index === targetIndex
        ? { ...step, depends_on: ['trigger'] }
        : step
    ));
    return {
      plan: { ...normalizedPlan, steps: nextSteps },
    };
  }

  const sourceIndex = nodeToStepIndex(normalizedPlan, sourceNodeId);
  if (sourceIndex === null || sourceIndex === targetIndex) {
    return { plan, error: '无法连接当前节点。' };
  }

  const sourceId = workflowStepKey(steps[sourceIndex], sourceIndex);
  const targetId = workflowStepKey(steps[targetIndex], targetIndex);
  const dependencyMap = new Map<string, string[]>();
  steps.forEach((step, index) => {
    dependencyMap.set(
      workflowStepKey(step, index),
      (step.depends_on ?? []).map(String),
    );
  });

  if (dependsOnStep(dependencyMap, sourceId, targetId)) {
    return {
      plan,
      error: '该连线会形成循环依赖，未应用修改。',
    };
  }

  const currentDependencies = (steps[targetIndex].depends_on ?? [])
    .map(String);
  if (currentDependencies.includes(sourceId)) {
    return { plan: normalizedPlan };
  }

  const nextSteps = steps.map((step, index) => (
    index === targetIndex
      ? {
          ...step,
          depends_on: [...currentDependencies, sourceId],
        }
      : step
  ));
  return {
    plan: { ...normalizedPlan, steps: nextSteps },
  };
}

export function disconnectWorkflowNodes(
  plan: StructuredPlan,
  sourceNodeId: string,
  targetNodeId: string,
): StructuredPlan {
  const normalizedPlan = ensureExplicitDependencies(plan);
  const steps = normalizedPlan.steps ?? [];
  const targetIndex = nodeToStepIndex(normalizedPlan, targetNodeId);

  if (targetIndex === null) {
    return normalizedPlan;
  }

  const sourceIndex = nodeToStepIndex(normalizedPlan, sourceNodeId);
  if (sourceIndex === null) {
    if (sourceNodeId !== 'trigger') {
      return normalizedPlan;
    }
    const nextSteps = steps.map((step, index) => (
      index === targetIndex
        ? {
            ...step,
            depends_on: (step.depends_on ?? [])
              .map(String)
              .filter((dependency) => dependency !== 'trigger'),
          }
        : step
    ));
    return { ...normalizedPlan, steps: nextSteps };
  }

  const sourceId = workflowStepKey(steps[sourceIndex], sourceIndex);
  const nextSteps = steps.map((step, index) => (
    index === targetIndex
      ? {
          ...step,
          depends_on: (step.depends_on ?? [])
            .map(String)
            .filter((dependency) => dependency !== sourceId),
        }
      : step
  ));

  return { ...normalizedPlan, steps: nextSteps };
}

function pythonString(value: unknown): string {
  return JSON.stringify(String(value ?? ''));
}

function firstText(...values: unknown[]): string | null {
  for (const value of values) {
    if (value === null || value === undefined) {
      continue;
    }
    const text = String(value).trim();
    if (text) {
      return text;
    }
  }
  return null;
}

function stepSelector(step: WorkflowStep): string | null {
  return firstText(
    step.selector,
    step.target,
    step.params?.selector,
    step.params?.locator,
  );
}

export function generatePlaywrightCode(
  plan: StructuredPlan,
): string {
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const lines: string[] = [
    '# Auto-generated Playwright preview',
    '# Edits are reflected locally until the workflow is saved.',
  ];

  steps.forEach((step, index) => {
    const action = String(step.action ?? '').toLowerCase();
    const params = step.params ?? {};
    const executor = String(step.executor ?? '').toLowerCase();
    if (executor && executor !== 'web') {
      lines.push('');
      if (executor === 'mcp' || action === 'call_tool') {
        lines.push(
          `# Step ${index + 1}: MCP · ${String(params.server ?? '应用')}`,
        );
        lines.push(
          `# Tool: ${String(params.tool ?? '未选择工具')} (${String(step.risk_level ?? 'risk unknown')})`,
        );
        return;
      }
      lines.push(
        `# Step ${index + 1}: ${getActionPresentation(action).label} (${executor})`,
      );
      lines.push(
        `# ${executor} step routed by the hybrid executor: ${action} ${JSON.stringify(params)}`,
      );
      return;
    }
    const selector = stepSelector(step);
    const target = firstText(step.target, selector, params.url);
    lines.push('');
    lines.push(
      `# Step ${index + 1}: ${getActionPresentation(action).label}`,
    );

    switch (action) {
      case 'navigate':
      case 'goto': {
        const url = firstText(params.url, step.target, params.href);
        lines.push(
          url
            ? `await page.goto(${pythonString(url)})`
            : '# Missing navigation URL',
        );
        break;
      }
      case 'click':
        lines.push(
          selector
            ? `await page.click(${pythonString(selector)})`
            : '# Missing click selector',
        );
        break;
      case 'fill': {
        const value = firstText(
          params.value,
          params.text,
          params.input,
          step.value,
        );
        lines.push(
          selector && value !== null
            ? `await page.fill(${pythonString(selector)}, ${pythonString(value)})`
            : '# Missing fill selector or value',
        );
        break;
      }
      case 'press': {
        const key = firstText(params.key, params.keys, params.value) ?? 'Enter';
        lines.push(
          selector
            ? `await page.press(${pythonString(selector)}, ${pythonString(key)})`
            : '# Missing press selector',
        );
        break;
      }
      case 'extract':
        lines.push(
          selector
            ? `data = await page.locator(${pythonString(selector)}).inner_text()`
            : '# Missing extract selector',
        );
        break;
      case 'compare':
        lines.push(`# Compare ${pythonString(firstText(params.field, target) ?? '')} with expected value`);
        lines.push(`log("Data comparison completed")`);
        break;
      case 'notify':
        lines.push(
          `log(${pythonString(firstText(params.message, target, 'Workflow finished') )})`,
        );
        break;
      case 'wait': {
        const timeout = firstText(params.timeout, params.ms);
        lines.push(
          selector
            ? `await page.wait_for_selector(${pythonString(selector)})`
            : timeout
              ? `await page.wait_for_timeout(${Math.max(0, Number(timeout) || 0)})`
              : '# Missing wait condition',
        );
        break;
      }
      case 'screenshot':
        lines.push(
          `await screenshot(${pythonString(firstText(step.target, target, `step_${index + 1}`))})`,
        );
        break;
      default:
        lines.push(
          `# Unsupported action ${pythonString(action || 'unknown')} for ${pythonString(target ?? '')}`,
        );
    }
  });

  if (steps.length === 0) {
    lines.push('', '# No executable steps');
    lines.push('pass');
  }

  return `${lines.join('\n')}\n`;
}

export function generateDesktopPreview(plan: StructuredPlan): string {
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  const lines: string[] = [
    '# Auto-generated desktop action preview',
    '# 由本机 Windows Agent 按顺序执行。',
  ];

  steps.forEach((step, index) => {
    const action = String(step.action ?? '').toLowerCase();
    const params = step.params ?? {};
    lines.push('');
    lines.push(
      `# Step ${index + 1}: ${getActionPresentation(action).label}`,
    );

    switch (action) {
      case 'launch_app': {
        const path = firstText(params.path, step.target);
        lines.push(
          path
            ? `launch_app(path=${pythonString(path)})`
            : '# Missing application path',
        );
        break;
      }
      case 'activate_window': {
        const title = firstText(params.title, step.target);
        lines.push(
          title
            ? `activate_window(title=${pythonString(title)})`
            : '# Missing window title',
        );
        break;
      }
      case 'click_element': {
        const name = firstText(
          params.name,
          params.automation_id,
          step.target,
        );
        lines.push(
          name
            ? `click_element(name=${pythonString(name)})`
            : '# Missing element name',
        );
        break;
      }
      case 'type_text': {
        const text = firstText(params.text, step.value);
        lines.push(
          text !== null
            ? `type_text(text=${pythonString(text)})`
            : '# Missing text',
        );
        break;
      }
      case 'press_hotkey': {
        const keys = firstText(params.keys, step.value);
        lines.push(
          keys
            ? `press_hotkey(keys=${pythonString(keys)})`
            : '# Missing hotkey',
        );
        break;
      }
      case 'read_text': {
        const name = firstText(
          params.name,
          params.automation_id,
          step.target,
        );
        lines.push(
          name
            ? `read_text(name=${pythonString(name)})`
            : '# Missing element name',
        );
        break;
      }
      case 'wait': {
        const seconds = firstText(
          params.seconds,
          params.timeout,
          params.ms,
        );
        lines.push(
          seconds
            ? `wait(seconds=${pythonString(seconds)})`
            : '# Missing wait duration',
        );
        break;
      }
      case 'screenshot':
        lines.push(
          `screenshot(name=${pythonString(firstText(params.name, step.target) ?? `step_${index + 1}`)})`,
        );
        break;
      case 'create_document': {
        const path = firstText(params.path, step.target);
        lines.push(
          path
            ? `create_document(path=${pythonString(path)})`
            : '# Missing document path',
        );
        break;
      }
      case 'close_app':
        lines.push(
          `close_app(target=${pythonString(firstText(params.target) ?? 'last')})`,
        );
        break;
      default:
        lines.push(
          `# Unsupported desktop action ${pythonString(action || 'unknown')}`,
        );
    }
  });

  if (steps.length === 0) {
    lines.push('', '# No executable steps');
  }

  return `${lines.join('\n')}\n`;
}
