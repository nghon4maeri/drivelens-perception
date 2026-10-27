/** Public labels shared by proposal status and measured timeline (§21.40). */
export const phaseLabel = (phase: string): string => ({
  VALIDATE_CONTEXT: 'Kiểm tra cấu hình', PREPARE_METADATA: 'Dựng danh sách frame từ metadata',
  LOAD_RAW: 'Lấy video gốc (cache hoặc S3)', LOAD_FEATURES: 'Tải/nạp embedding DINO và CLIP',
  LOAD_REVIEW_STATE: 'Đọc lịch sử Giữ/Bỏ', LOAD_MODEL: 'Lấy model detector', STRATEGY: 'Chuẩn bị thuật toán',
  CLIP_TEXT_CACHE: 'Tra cache vector truy vấn', CLIP_MODEL: 'Nạp mô hình CLIP text',
  CLIP_TEXT_ENCODE: 'Mã hóa prompt chưa có cache', ODD_CLIP_GATE: 'Lọc các nhánh ODD bằng CLIP',
  ODD_SELECT: 'Chọn coverage và diversity DINO', INFER_UNCERTAINTY: 'Chạy detector trên shortlist',
  INFERRING_UNCERTAINTY: 'Chạy detector trên shortlist', EXPLAIN: 'Chuẩn bị giải thích', PUBLISH: 'Lưu kết quả đề xuất',
}[phase] ?? (phase.startsWith('COMPUTE_') ? 'Tính trường dẫn xuất' : 'đang tính đề xuất'))
