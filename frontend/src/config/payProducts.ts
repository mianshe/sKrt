export type PayProductType = "ocr_calls" | "glm_ocr_tokens" | "embedding_tokens";
export type PayChannel = "wechat_native" | "alipay_qr" | "paypal";

export type PayProduct = {
  key: string;
  type: PayProductType;
  name: string;
  priceCny: number;
  calls?: number;
  tokens?: number;
};

export const PAY_PRODUCTS: PayProduct[] = [
  { key: "A", type: "ocr_calls", name: "OCR 次数包 A", calls: 500, priceCny: 9.9 },
  { key: "B", type: "ocr_calls", name: "OCR 次数包 B", calls: 2000, priceCny: 29.9 },
  { key: "C", type: "ocr_calls", name: "OCR 次数包 C", calls: 5000, priceCny: 59.9 },
  { key: "T1", type: "glm_ocr_tokens", name: "GLM-OCR Token 包 1", tokens: 20000, priceCny: 19.9 },
  { key: "T2", type: "glm_ocr_tokens", name: "GLM-OCR Token 包 2", tokens: 80000, priceCny: 59.9 },
  { key: "T3", type: "glm_ocr_tokens", name: "GLM-OCR Token 包 3", tokens: 200000, priceCny: 129.9 },
  { key: "S1", type: "embedding_tokens", name: "Embedding-3 Token 包 1", tokens: 10000, priceCny: 19.9 },
  { key: "S2", type: "embedding_tokens", name: "Embedding-3 Token 包 2", tokens: 40000, priceCny: 59.9 },
  { key: "S3", type: "embedding_tokens", name: "Embedding-3 Token 包 3", tokens: 90000, priceCny: 129.9 },
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
  if (product.type === "glm_ocr_tokens") {
    return `${product.tokens ?? 0} token，￥${product.priceCny}`;
  }
  return `${product.tokens ?? 0} token，￥${product.priceCny}`;
}

export function formatPayChannel(channel: PayChannel): string {
  if (channel === "wechat_native") return "微信";
  if (channel === "alipay_qr") return "支付宝";
  return "PayPal";
}
