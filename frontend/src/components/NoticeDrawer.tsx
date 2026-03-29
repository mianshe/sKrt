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
    title: "服务器与外部 OCR",
    date: "2026-03-28",
    body:
      "因资金限制服务器只有2c4g，有可能遇上使用高峰。纯扫描件（指纸质书扫描，鼠标指针放上去无变化的那种）超过10MB会提示是否使用外部 OCR 先提取文字，可以取消。每个新注册用户最开始赠送100次OCR请求次数，同ip最多三个邮箱获得新人礼，防止换邮箱刷次数（扫1页可能要算不止一次请求次数，直接调用 OCR 服务商官网 API，以运营商为准）。",
  },
  {
    title: "数据与存储",
    date: "2026-03-28",
    body:
      "处理后的文本会保留数据，包括原件副本+过程JSON，默认保存在本地浏览器数据内，可在应用内下载解析后的结果文件。本地保存失败的话数据会自动保存云端，因资源有限，每个账号云端容量100MB，最长保留30天。测试项目云端有限，超额会限制使用。游客使用数据只能保存本地。",
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

