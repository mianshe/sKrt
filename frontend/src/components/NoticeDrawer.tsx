import React from "react";
import { X, Bell } from "lucide-react";

type Notice = {
  title: string;
  date: string;
  body: string;
  pinned?: boolean;
};

type Props = {
  open: boolean;
  onClose: () => void;
  widthPx?: number;
  notices?: Notice[];
};

const DEFAULT_NOTICES: Notice[] = [
  {
    title: "置顶公告：项目方法论说明",
    date: "2026-04-26",
    pinned: true,
    body:
      "该项目区别于常规知识库技术的方法论在于：该项目侧重于 Agent 直接全面分析文本内容，而非市面上普遍的索引机制。市面上的分析常规做法是注意力机制提取关键词生成向量再进行推理和序列化表达，该项目的做法是地图式理解章节后分块对文字进行处理，将高容量文本拆分小块存入临时数据库建立知识图谱、再采用高知识储备的 Agent 理解再转达给空白 Agent，选择表达给空白 Agent 最优的表达方式作为全面理解的主要内容，包含普通的文字处理加上了认知层面的概念化传递。类似于同样一份知识放在不同学校内，以教学成果评选重点学校那样，属于教育学理念雏形模型算法在文字处理上的使用。",
  },
  {
    title: "置顶公告二：解析与问答说明",
    date: "2026-04-27",
    pinned: true,
    body:
      "不考虑时间和内存，单就算法而言的话，无论多大的文档都能解析，测试文件最大的文档是 63MB。直接用云主机本地的库来解析是免费的，要精度高一点、快一点可选付费 API。解析区的“执行摘要”是解析完全文总结出来的文本，往下翻有该文档每个章节的解析。问答区属于基础知识库索引运用，与一般知识库不同的是可以直接上传带有案例材料 + 问题的文本，让模型先拆题，再根据知识库内容回答。",
  },
  {
    title: "服务器与 OCR 说明",
    date: "2026-03-28",
    body:
      "当前服务器资源有限，使用高峰期可能出现排队。扫描版 PDF 在较大体积下可能触发外部 OCR 确认提示，可按需继续或取消。",
  },
  {
    title: "存储与本地副本",
    date: "2026-03-28",
    body:
      "网页端默认会将文档及处理结果同步到云端，便于后续检索和问答。但云主机存储容量有限，每个账号仅有 100MB 存储，且过程文件使用的存储容量可能是原件的几倍，具体取决于文本复杂情况。于是本项目也有 EXE 和 APP 客户端，用以将原件和过程文件直接存储在用户本地，保护隐私的同时，也不会因为云端被占满而导致项目无法使用。",
  },
  {
    title: "支付说明",
    date: "2026-04-03",
    body:
      "若支付页面显示随机尾号金额，请在订单有效时间内按页面金额完成支付，以便系统自动匹配订单。",
  },
];

export default function NoticeDrawer({ open, onClose, widthPx = 400, notices = DEFAULT_NOTICES }: Props) {
  return (
    <>
      {open && <div className="fixed inset-0 z-[60] bg-slate-900/40 backdrop-blur-sm transition-opacity" onClick={onClose} />}

      <aside
        className={`fixed right-0 top-0 z-[70] h-full transform transition-transform duration-300 ease-in-out ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
        style={{ width: `${widthPx}px`, maxWidth: "90vw" }}
      >
        <div className="m-2 flex h-full flex-col bg-white neo-box">
          <div className="flex items-center justify-between border-b-4 border-slate-900 bg-yellow-400 p-6">
            <div className="flex items-center gap-3">
              <div className="bg-white p-2 neo-box-sm">
                <Bell size={24} />
              </div>
              <h2 className="text-2xl font-black uppercase tracking-tighter">系统公告</h2>
            </div>
            <button onClick={onClose} className="bg-white p-2 neo-button-sm hover:bg-pink-400 hover:text-white">
              <X size={20} />
            </button>
          </div>

          <div className="custom-scrollbar flex-1 space-y-6 overflow-y-auto bg-slate-50 p-6">
            {notices.map((n, idx) => (
              <div key={idx} className="rotate-[-0.5deg] bg-white p-6 transition-transform hover:rotate-0 neo-box">
                <div className="mb-3 flex items-start justify-between gap-3 border-b-2 border-slate-900 pb-2">
                  <div className="flex items-center gap-2">
                    <h3 className="text-lg font-black leading-tight">{n.title}</h3>
                    {n.pinned ? (
                      <span className="bg-pink-500 px-2 py-1 text-[10px] font-black uppercase tracking-widest text-white neo-box-sm">
                        置顶
                      </span>
                    ) : null}
                  </div>
                  <span className="bg-blue-400 px-2 py-1 text-[10px] font-bold whitespace-nowrap text-white neo-box-sm">
                    {n.date}
                  </span>
                </div>
                <p className="whitespace-pre-wrap text-sm font-bold leading-relaxed text-slate-700">{n.body}</p>
              </div>
            ))}
          </div>

          <div className="bg-slate-900 p-6 text-white">
            <button onClick={onClose} className="w-full text-sm font-black uppercase tracking-widest bg-pink-500 neo-button">
              知道了
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
