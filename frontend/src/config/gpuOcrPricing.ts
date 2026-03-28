export type GpuOcrPagePack = {
  key: "A" | "B" | "C";
  name: string;
  pages: number;
  priceCny: number;
  pricePerPageCny: number;
};

export const GPU_OCR_PAGE_PACKS: GpuOcrPagePack[] = [
  { key: "A", name: "次包A", pages: 500, priceCny: 9.9, pricePerPageCny: 9.9 / 500 },
  { key: "B", name: "次包B", pages: 2000, priceCny: 29.9, pricePerPageCny: 29.9 / 2000 },
  { key: "C", name: "次包C", pages: 5000, priceCny: 59.9, pricePerPageCny: 59.9 / 5000 },
];

export const GPU_OCR_REDEEM_PAGES = 500;

