export type PayProductType = "ocr_calls" | "ocr_tokens" | "cloud_capacity";
export type PayChannel = "wechat_native" | "alipay_qr" | "paypal";

export type PayProduct = {
  key: string;
  type: PayProductType;
  name: string;
  priceCny: number;
  calls?: number;
  tokens?: number;
  docBonus?: number;
  storageBytes?: number;
};

export const PAY_PRODUCTS: PayProduct[] = [
  { key: "A", type: "ocr_calls", name: "OCR 次数包 A", calls: 500, priceCny: 9.9 },
  { key: "B", type: "ocr_calls", name: "OCR 次数包 B", calls: 2000, priceCny: 29.9 },
  { key: "C", type: "ocr_calls", name: "OCR 次数包 C", calls: 5000, priceCny: 59.9 },
  { key: "T1", type: "ocr_tokens", name: "复杂 OCR Token 包 1", tokens: 20000, priceCny: 19.9 },
  { key: "T2", type: "ocr_tokens", name: "复杂 OCR Token 包 2", tokens: 80000, priceCny: 59.9 },
  { key: "T3", type: "ocr_tokens", name: "复杂 OCR Token 包 3", tokens: 200000, priceCny: 129.9 },
  {
    key: "S1",
    type: "cloud_capacity",
    name: "云端容量包 1",
    docBonus: 500,
    storageBytes: 5 * 1024 * 1024 * 1024,
    priceCny: 19.9,
  },
  {
    key: "S2",
    type: "cloud_capacity",
    name: "云端容量包 2",
    docBonus: 2000,
    storageBytes: 20 * 1024 * 1024 * 1024,
    priceCny: 59.9,
  },
  {
    key: "S3",
    type: "cloud_capacity",
    name: "云端容量包 3",
    docBonus: 5000,
    storageBytes: 50 * 1024 * 1024 * 1024,
    priceCny: 129.9,
  },
];

export function getPayProduct(type: PayProductType, key: string): PayProduct | undefined {
  return PAY_PRODUCTS.find((item) => item.type === type && item.key === key);
}

export function listPayProducts(type: PayProductType): PayProduct[] {
  return PAY_PRODUCTS.filter((item) => item.type === type);
}

export function describePayProduct(product: PayProduct): string {
  if (product.type === "ocr_calls") {
    return `${product.calls ?? 0} 次，￥${product.priceCny}`;
  }
  if (product.type === "ocr_tokens") {
    return `${product.tokens ?? 0} token，￥${product.priceCny}`;
  }
  const storageGb = Math.round((product.storageBytes ?? 0) / (1024 * 1024 * 1024));
  return `+${product.docBonus ?? 0} 文档 / ${storageGb}GB，￥${product.priceCny}`;
}

export function formatPayChannel(channel: PayChannel): string {
  if (channel === "wechat_native") return "微信";
  if (channel === "alipay_qr") return "支付宝";
  return "PayPal";
}
