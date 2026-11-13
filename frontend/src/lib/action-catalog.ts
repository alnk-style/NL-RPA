import type { StructuredPlan, WorkflowStep } from '../types/workflow';

export interface ActionDefinition {
  id: string;
  executor: string;
  title: string;
  description: string;
  category: string;
  risk_level: string;
  params_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  requires: Record<string, boolean>;
  catalog_version: string;
  legacy_only: boolean;
}

export interface ActionCatalog {
  version: string;
  actions: ActionDefinition[];
}

export interface ActionFormField {
  key: string;
  label: string;
  control: 'text' | 'number' | 'boolean' | 'enum' | 'json';
  required: boolean;
  description: string;
  options: string[];
}

export interface VariableHint {
  name: string;
  expression: string;
}

function schemaType(schema: Record<string, unknown>): string[] {
  const value = schema.type;
  if (Array.isArray(value)) {
    return value.map(String);
  }
  return typeof value === 'string' ? [value] : [];
}

export function schemaToActionFields(
  schema: Record<string, unknown> | null | undefined,
): ActionFormField[] {
  if (!schema || typeof schema !== 'object') {
    return [];
  }
  const properties =
    schema.properties && typeof schema.properties === 'object'
      ? schema.properties as Record<string, Record<string, unknown>>
      : {};
  const required = new Set(
    Array.isArray(schema.required) ? schema.required.map(String) : [],
  );

  return Object.entries(properties).map(([key, raw]) => {
    const definition = raw && typeof raw === 'object' ? raw : {};
    const types = schemaType(definition);
    const enumValues = Array.isArray(definition.enum)
      ? definition.enum.map(String)
      : [];
    let control: ActionFormField['control'] = 'text';
    if (types.includes('boolean')) {
      control = 'boolean';
    } else if (types.includes('integer') || types.includes('number')) {
      control = 'number';
    } else if (types.includes('array') || types.includes('object')) {
      control = 'json';
    } else if (enumValues.length > 0) {
      control = 'enum';
    }
    return {
      key,
      label: String(definition.title || key),
      control,
      required: required.has(key),
      description: String(definition.description || ''),
      options: enumValues,
    };
  });
}

function collectExpressions(value: unknown, output: string[]): void {
  if (typeof value === 'string') {
    for (const match of value.matchAll(/\$\{([^}]+)\}/g)) {
      const name = match[1].trim();
      if (name && !output.includes(name)) {
        output.push(name);
      }
    }
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item) => collectExpressions(item, output));
    return;
  }
  if (value && typeof value === 'object') {
    Object.values(value).forEach((item) => collectExpressions(item, output));
  }
}

export function collectVariableHints(plan: StructuredPlan): VariableHint[] {
  const names: string[] = [];
  const append = (name: string) => {
    const value = name.trim();
    if (value && !names.includes(value)) {
      names.push(value);
    }
  };
  for (const variable of Array.isArray(plan.variables) ? plan.variables : []) {
    if (typeof variable === 'string') {
      append(variable);
    } else if (variable && typeof variable === 'object') {
      append(String((variable as Record<string, unknown>).name ?? ''));
    }
  }
  for (const step of Array.isArray(plan.steps) ? plan.steps : []) {
    collectExpressions(step.params, names);
    append(String(step.result_var ?? ''));
    append(String(step.output_var ?? ''));
  }
  return names.map((name) => ({ name, expression: `\${${name}}` }));
}

export function actionOptionsForExecutor(
  catalog: ActionCatalog | null,
  executor: string,
): ActionDefinition[] {
  return (catalog?.actions ?? []).filter(
    (action) => action.executor === executor,
  );
}

export function defaultActionForExecutor(
  catalog: ActionCatalog | null,
  executor: string,
): string {
  return actionOptionsForExecutor(catalog, executor)[0]?.id
    ?? (
      executor === 'transform'
        ? 'filter'
        : executor === 'email'
          ? 'send'
          : ''
    );
}

export function executorUsesCatalogForm(executor: string): boolean {
  return [
    'web',
    'transform',
    'files',
    'email',
    'windows',
  ].includes(executor);
}

export function cloneStepParams(step: WorkflowStep): Record<string, unknown> {
  return { ...(step.params ?? {}) };
}
