import { type ReactNode, useMemo } from "react";
import { createPortal } from "react-dom";

type Props = {
  children: ReactNode;
};

export default function Portal({ children }: Props) {
  const canUseDom = typeof document !== "undefined" && typeof window !== "undefined";
  const mountNode = useMemo(() => (canUseDom ? document.body : null), [canUseDom]);
  if (!mountNode) return null;
  return createPortal(children, mountNode);
}

