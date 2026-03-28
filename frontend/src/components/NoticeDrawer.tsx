type Notice = {
  title: string;
  date: string;
  body: string;
};

type Props = {
  open: boolean;
  onClose: () => void;
  widthPx?: number;
  notices?: Notice[];
};

const DEFAULT_NOTICES: Notice[] = [
  {
    title: "公告栏",
    date: "2026-03-27",
    body: "这是不会遮挡上传框的侧边公告栏。后续你可以把这里的文案快速改掉并重新部署前端。",
  },
  {
    title: "使用提示",
    date: "2026-03-27",
    body: "外部 OCR 调用次数额度显示在顶部右侧；连续点击额度 6 次可打开兑换次数入口。",
  },
];

export default function NoticeDrawer({ open, onClose, widthPx = 360, notices = DEFAULT_NOTICES }: Props) {
  const w = `${widthPx}px`;
  return (
    <>
      {open && <div className="fixed inset-0 z-30 bg-black/30 md:hidden" onClick={onClose} />}
      <aside
        className={`fixed right-0 top-0 z-40 h-full transform bg-white/90 shadow-2xl ring-1 ring-slate-200 backdrop-blur transition-transform duration-200 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        style={{ width: w, maxWidth: "88vw" }}
        aria-hidden={!open}
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between border-b border-slate-200/70 px-4 py-3">
            <div>
              <div className="text-sm font-semibold text-slate-900">公告</div>
              <div className="text-[11px] text-slate-500">点击空白处或右上角关闭</div>
            </div>
            <button
              type="button"
              className="rounded-xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
              onClick={onClose}
            >
              关闭
            </button>
          </div>

          <div className="flex-1 overflow-auto px-4 py-3">
            <div className="space-y-3">
              {notices.map((n, idx) => (
                <div key={`${n.title}-${idx}`} className="rounded-2xl bg-white/80 p-3 ring-1 ring-slate-200">
                  <div className="flex items-baseline justify-between gap-3">
                    <div className="text-sm font-semibold text-slate-900">{n.title}</div>
                    <div className="text-[11px] text-slate-400">{n.date}</div>
                  </div>
                  <div className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-600">{n.body}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}

