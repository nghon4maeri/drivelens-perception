/** Lỗi API chuẩn hóa dùng chung cho client local (v1) và pilot (v2); tách file để không có vòng import giữa api.ts và pilot_api.ts. */
export class ApiError extends Error {
  constructor(public code: string, message: string, public sources: { source_id: string; kind: string; problem: 'missing' | 'changed'; n_samples: number }[] = [],
              public details: Record<string, unknown> = {}, public status?: number) { super(message) }
}
