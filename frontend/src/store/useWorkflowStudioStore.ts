import { create } from 'zustand';

import type {
  StructuredPlan,
  WorkflowRecord,
  WorkflowStep,
} from '../types/workflow';

type BottomTab = 'logs' | 'screenshot' | 'variables' | 'data';
type StudioPanel = 'variables' | 'steps';

interface WorkflowStudioState {
  workflowId: number | null;
  workflow: WorkflowRecord | null;
  baseVersionId: number | null;
  baseRevision: number;
  draft: StructuredPlan;
  dirty: boolean;
  selectedStepId: number | null;
  panel: StudioPanel;
  bottomTab: BottomTab;
  past: StructuredPlan[];
  future: StructuredPlan[];
  canUndo: boolean;
  canRedo: boolean;
  load: (workflow: WorkflowRecord, version?: { id: number } | null) => void;
  reset: () => void;
  patchStep: (stepId: number, patch: Partial<WorkflowStep>) => void;
  replaceDraft: (plan: StructuredPlan) => void;
  addStep: (step: WorkflowStep) => void;
  moveStep: (from: number, to: number) => void;
  selectStep: (stepId: number | null) => void;
  setPanel: (panel: StudioPanel) => void;
  setBottomTab: (tab: BottomTab) => void;
  undo: () => void;
  redo: () => void;
  markSaved: (revision: number, plan: StructuredPlan) => void;
  markSaveFailed: () => void;
}

const EMPTY_PLAN: StructuredPlan = {
  execution_mode: 'structured',
  variables: [],
  steps: [],
};

function clonePlan(plan: StructuredPlan): StructuredPlan {
  return JSON.parse(JSON.stringify(plan)) as StructuredPlan;
}

function stepsOf(plan: StructuredPlan): WorkflowStep[] {
  return Array.isArray(plan.steps) ? plan.steps : [];
}

export const useWorkflowStudioStore = create<WorkflowStudioState>((set, get) => ({
  workflowId: null,
  workflow: null,
  baseVersionId: null,
  baseRevision: 0,
  draft: clonePlan(EMPTY_PLAN),
  dirty: false,
  selectedStepId: null,
  panel: 'steps',
  bottomTab: 'logs',
  past: [],
  future: [],
  canUndo: false,
  canRedo: false,

  load: (workflow, version = null) => {
    const source = (
      workflow.structured_plan
      ?? EMPTY_PLAN
    ) as StructuredPlan;
    set({
      workflowId: workflow.id,
      workflow,
      baseVersionId: version?.id ?? workflow.active_version_id ?? null,
      baseRevision: workflow.draft_revision ?? 0,
      draft: clonePlan(source),
      dirty: false,
      selectedStepId: null,
      panel: 'steps',
      bottomTab: 'logs',
      past: [],
      future: [],
      canUndo: false,
      canRedo: false,
    });
  },

  reset: () => set({
    workflowId: null,
    workflow: null,
    baseVersionId: null,
    baseRevision: 0,
    draft: clonePlan(EMPTY_PLAN),
    dirty: false,
    selectedStepId: null,
    panel: 'steps',
    bottomTab: 'logs',
    past: [],
    future: [],
    canUndo: false,
    canRedo: false,
  }),

  patchStep: (stepId, patch) => {
    const state = get();
    const previous = clonePlan(state.draft);
    const next = clonePlan(state.draft);
    next.steps = stepsOf(next).map((step) => (
      Number(step.step_id) === stepId ? { ...step, ...patch } : step
    ));
    set({
      draft: next,
      dirty: true,
      past: [...state.past, previous],
      future: [],
      canUndo: true,
      canRedo: false,
    });
  },

  replaceDraft: (plan) => {
    const state = get();
    set({
      draft: clonePlan(plan),
      dirty: true,
      selectedStepId: null,
      past: [...state.past, clonePlan(state.draft)],
      future: [],
      canUndo: true,
      canRedo: false,
    });
  },

  addStep: (step) => {
    const state = get();
    const previous = clonePlan(state.draft);
    const next = clonePlan(state.draft);
    const steps = stepsOf(next);
    const nextId = Math.max(0, ...steps.map((item) => Number(item.step_id ?? 0))) + 1;
    steps.push({
      ...step,
      step_id: step.step_id ?? nextId,
      depends_on: (
        step.depends_on
        ?? (steps.length ? [steps[steps.length - 1].step_id] : [])
      ).filter(
        (item): item is string | number =>
          typeof item === 'string' || typeof item === 'number',
      ),
    });
    next.steps = steps;
    set({
      draft: next,
      dirty: true,
      selectedStepId: Number(steps[steps.length - 1].step_id),
      past: [...state.past, previous],
      future: [],
      canUndo: true,
      canRedo: false,
    });
  },

  moveStep: (from, to) => {
    const state = get();
    const steps = stepsOf(clonePlan(state.draft));
    if (
      from < 0
      || to < 0
      || from >= steps.length
      || to >= steps.length
      || from === to
    ) {
      return;
    }
    const previous = clonePlan(state.draft);
    const [moved] = steps.splice(from, 1);
    steps.splice(to, 0, moved);
    set({
      draft: { ...state.draft, steps },
      dirty: true,
      past: [...state.past, previous],
      future: [],
      canUndo: true,
      canRedo: false,
    });
  },

  selectStep: (selectedStepId) => set({ selectedStepId }),
  setPanel: (panel) => set({ panel }),
  setBottomTab: (bottomTab) => set({ bottomTab }),

  undo: () => {
    const state = get();
    const previous = state.past[state.past.length - 1];
    if (!previous) {
      return;
    }
    set({
      draft: clonePlan(previous),
      past: state.past.slice(0, -1),
      future: [clonePlan(state.draft), ...state.future],
      dirty: true,
      canUndo: state.past.length > 1,
      canRedo: true,
    });
  },

  redo: () => {
    const state = get();
    const next = state.future[0];
    if (!next) {
      return;
    }
    set({
      draft: clonePlan(next),
      past: [...state.past, clonePlan(state.draft)],
      future: state.future.slice(1),
      dirty: true,
      canUndo: true,
      canRedo: state.future.length > 1,
    });
  },

  markSaved: (revision, plan) => set({
    baseRevision: revision,
    draft: clonePlan(plan),
    dirty: false,
    past: [],
    future: [],
    canUndo: false,
    canRedo: false,
  }),

  markSaveFailed: () => set({ dirty: true }),
}));

export default useWorkflowStudioStore;
