// Shapes mirroring the FastAPI /api responses.

export type Strategy = 'label' | 'region';
export type Relation = 'right' | 'below';
export type ValueType = 'string' | 'date' | 'number';

export interface OutputField {
  key: string;
  display_name: string;
  value_type: ValueType;
  sort_order: number;
  aliases: string[]; // "read labels" / synonyms
}

export interface FieldSuggestion {
  category: string;
  suggested_key: string;
  read_labels: string[];
  value: string | null;
  page: number | null;
  bbox: number[] | null;
  value_type: ValueType;
}

export interface Mapping {
  output: string;
  strategy: Strategy;
  label: string | null;
  relation: Relation;
  value_type: ValueType;
  page: number | null;
  bbox: number[] | null; // [x0, top, x1, bottom] in PDF points
}

export interface Vendor {
  id: number;
  identifier: string;
  name: string;
  identifier_kind: string;
  match_keywords: string[];
  mappings: Mapping[];
}

export interface DetectedVendor {
  id: number;
  identifier: string;
  name: string;
}

export interface PageSize {
  width: number;
  height: number;
}

export interface DocumentInfo {
  doc_id: string;
  n_pages: number;
  pages: PageSize[];
  ocr_used: boolean;
  detected_vendor: DetectedVendor | null;
}

export interface FieldVal {
  value: string | null;
  raw: string | null;
  page: number | null;
  bbox: number[] | null;
  confidence: number;
  source_label: string | null;
}

export interface ReadField extends FieldVal {
  output: string;
  source: string; // template-label | template-region | none
}

export interface Suggestion {
  kind: string;
  field: FieldVal;
}

export interface LineItem {
  description: FieldVal;
  quantity: FieldVal;
  unit_price: FieldVal;
  amount: FieldVal;
}

export interface ReadResult {
  fields: ReadField[];
  suggestions: Suggestion[];
  lines: LineItem[];
  meta: { pages: number; ocr_used: boolean; fields_found: number; fields_total: number };
}

export interface VendorPayload {
  identifier: string;
  name: string;
  identifier_kind: string;
  match_keywords: string[];
  mappings: Mapping[];
}
