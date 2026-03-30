export type GpuOcrCallPack = {
  key: "A" | "B" | "C";
  name: string;
  /** 外部 OCR 扣减单位：次数（与后端 paid_balance 一致） */
  calls: number;
  priceCny: number;
  pricePerCallCny: number;
};

export const GPU_OCR_CALL_PACKS: GpuOcrCallPack[] = [
  { key: "A", name: "次包A", calls: 500, priceCny: 9.9, pricePerCallCny: 9.9 / 500 },
  { key: "B", name: "次包B", calls: 2000, priceCny: 29.9, pricePerCallCny: 29.9 / 2000 },
  { key: "C", name: "次包C", calls: 5000, priceCny: 59.9, pricePerCallCny: 59.9 / 5000 },
];

/** @deprecated 使用 GPU_OCR_CALL_PACKS */
export const GPU_OCR_PAGE_PACKS = GPU_OCR_CALL_PACKS;
/** @deprecated 使用 GpuOcrCallPack */
export type GpuOcrPagePack = GpuOcrCallPack;

export const GPU_OCR_REDEEM_CALLS = 500;
/** @deprecated 使用 GPU_OCR_REDEEM_CALLS */
export const GPU_OCR_REDEEM_PAGES = GPU_OCR_REDEEM_CALLS;
