export type StudioFieldControl =
  | 'text'
  | 'textarea'
  | 'number'
  | 'switch'
  | 'select'
  | 'array'
  | 'object'
  | 'credential'
  | 'variable'
  | 'condition'
  | 'json';

export interface StudioSchemaField {
  key: string;
  label: string;
  control: StudioFieldControl;
  required: boolean;
  description: string;
  options: string[];
  schema: Record<string, unknown>;
  fallback: boolean;
}

function schemaTypes(schema: Record<string, unknown>): string[] {
  const value = schema.type;
  if (Array.isArray(value)) {
    return value.map(String);
  }
  return typeof value === 'string' ? [value] : [];
}

export function schemaToStudioFields(
  schema: Record<string, unknown> | null | undefined,
): StudioSchemaField[] {
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
    const types = schemaTypes(definition);
    const format = String(definition.format ?? '');
    const options = Array.isArray(definition.enum)
      ? definition.enum.map(String)
      : [];
    let control: StudioFieldControl = 'text';
    let fallback = false;
    if (format === 'credential') {
      control = 'credential';
    } else if (format === 'variable') {
      control = 'variable';
    } else if (format === 'condition') {
      control = 'condition';
    } else if (format === 'multiline') {
      control = 'textarea';
    } else if (
      definition.oneOf
      || definition.anyOf
      || (!types.length && !options.length)
    ) {
      control = 'json';
      fallback = true;
    } else if (types.includes('boolean')) {
      control = 'switch';
    } else if (types.includes('integer') || types.includes('number')) {
      control = 'number';
    } else if (types.includes('array')) {
      control = 'array';
    } else if (types.includes('object')) {
      control = 'object';
    } else if (options.length) {
      control = 'select';
    }
    return {
      key,
      label: String(definition.title || key),
      control,
      required: required.has(key),
      description: String(definition.description || ''),
      options,
      schema: definition,
      fallback,
    };
  });
}

export function buildCondition(input: {
  variable: string;
  operator: string;
  value: unknown;
  cast?: string;
}): Record<string, unknown> {
  let literal = input.value;
  if (input.cast === 'number' || input.cast === 'integer') {
    literal = Number(input.value);
  } else if (input.cast === 'boolean') {
    literal = String(input.value).toLowerCase() === 'true';
  }
  return {
    left: { var: input.variable.trim() },
    operator: input.operator,
    right: { literal },
    ...(input.cast ? { cast: input.cast } : {}),
  };
}
