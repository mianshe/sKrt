import type { MouseEvent, ReactNode } from "react";
import Portal from "./Portal";

type Props = {
  open: boolean;
  onClose?: () => void;
  children: ReactNode;
  overlayClassName?: string;
  panelClassName?: string;
  closeOnBackdrop?: boolean;
};

const defaultOverlayClassName = "fixed inset-0 z-[60] overflow-y-auto bg-black/30 p-4 sm:p-6";
const defaultPanelClassName =
  "relative my-auto w-full max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200 sm:max-h-[calc(100dvh-3rem)]";

export default function ModalShell({
  open,
  onClose,
  children,
  overlayClassName = defaultOverlayClassName,
  panelClassName = defaultPanelClassName,
  closeOnBackdrop = true,
}: Props) {
  if (!open) return null;

  const handleBackdropClick = () => {
    if (closeOnBackdrop) onClose?.();
  };

  const stopPropagation = (event: MouseEvent<HTMLDivElement>) => {
    event.stopPropagation();
  };

  return (
    <Portal>
      <div className={overlayClassName} role="presentation" onClick={handleBackdropClick}>
        <div className="flex min-h-[calc(100dvh-2rem)] items-center justify-center sm:min-h-[calc(100dvh-3rem)]">
          <div className={panelClassName} onClick={stopPropagation}>
            {children}
          </div>
        </div>
      </div>
    </Portal>
  );
}
