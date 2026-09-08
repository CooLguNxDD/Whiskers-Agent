/**
 * Minimal hand-written OpenAPI 3.0 document types for catalog export responses.
 * Covers fields produced by `build_openapi_from_ops` — not a full spec mirror.
 */

export type OpenAPIHttpMethod =
  | "get"
  | "post"
  | "put"
  | "patch"
  | "delete"
  | "head"
  | "options"
  | "trace"

export interface OpenAPIInfoObject {
  title: string
  version: string
  description?: string
}

export interface OpenAPIMediaTypeObject {
  schema?: Record<string, unknown>
}

export interface OpenAPIRequestBodyObject {
  required?: boolean
  content?: Record<string, OpenAPIMediaTypeObject>
}

export interface OpenAPIResponseObject {
  description: string
  content?: Record<string, OpenAPIMediaTypeObject>
}

export interface OpenAPIOperationObject {
  operationId?: string
  summary?: string
  description?: string
  tags?: string[]
  requestBody?: OpenAPIRequestBodyObject
  responses?: Record<string, OpenAPIResponseObject>
}

export type OpenAPIPathItemObject = Partial<
  Record<OpenAPIHttpMethod, OpenAPIOperationObject>
>

export interface OpenAPIObject {
  openapi: string
  info: OpenAPIInfoObject
  paths: Record<string, OpenAPIPathItemObject>
}