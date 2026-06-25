"""
通知邮箱读取 —— PRISMA / EnMAP 的下载链接都由邮件投递,这里统一从 IMAP 收件箱
里提取下载链接,供各下载器在订单完成后取真实下载地址。

背景:
  - PRISMA:订单 COMPLETED 后,邮件推送带令牌的 HTTP 直链
      http://prisma.asi.it/Products/<token>/PRS_L2D_STD_<start>_<stop>_0001.zip
      (令牌自带授权,匿名可下;链接不在任何 API/目录里,只走邮件)
  - EnMAP:EOWEB「Delivery Notice」邮件(发件 NRSDL-Operations@dlr.de)给出 FTPS 链接
      ftps://<user>@download.dsda.dlr.de//dims_op_oc_oc-en_<id>_1.tar.gz

配置:config/credentials.yaml 顶层 `notifications` 段:
  notifications:
    imap_host: imap.qq.com
    imap_user: 13812567@qq.com
    imap_password: <IMAP 授权码>     # QQ 用「授权码」,非登录密码

匹配键:
  - PRISMA:L2D 文件名里的 <start>_<stop>(14位时间戳对)= 下单输入 L0 景
      PRS_L0__EO_OFFL_<start>_<stop>_… 的时间戳,用它把链接对回 pending 订单。
  - EnMAP:dims_op_oc_oc-en_<id>(交付包名)。
"""
import re
import html
import email
import imaplib
from pathlib import Path
from email.header import decode_header
from typing import Optional, List, Dict

import yaml

_PRISMA_LINK = re.compile(r'https?://prisma\.asi\.it/Products/\S+?\.zip', re.I)
_FTPS_LINK   = re.compile(r'ftps?://[^\s"\'<>)\]]+?\.(?:tar\.gz|tgz|zip)', re.I)
_TS_PAIR     = re.compile(r'(\d{14})[_T](\d{14})')          # <start>_<stop>
_DIMS_NAME   = re.compile(r'dims_op_oc_oc-en_\w+')

_ROOT = Path(__file__).resolve().parent.parent
_CRED_PATH = _ROOT / "config" / "credentials.yaml"


def load_imap_config(config_path: Optional[Path] = None) -> Optional[Dict]:
    """从 credentials.yaml 的 notifications 段读 IMAP 配置;缺失返回 None。"""
    p = Path(config_path) if config_path else _CRED_PATH
    try:
        cfg = (yaml.safe_load(p.read_text("utf-8")) or {}).get("notifications") or {}
    except Exception:
        return None
    host = cfg.get("imap_host"); user = cfg.get("imap_user"); pwd = cfg.get("imap_password")
    if not (host and user and pwd):
        return None
    return {"host": host, "user": user, "password": pwd,
            "port": int(cfg.get("imap_port", 993)),
            "mailbox": cfg.get("imap_mailbox", "INBOX")}


def timestamps_from_filename(name: str) -> Optional[str]:
    """从 L0/L2D 文件名里抽出 <start>_<stop> 时间戳对,作为 PRISMA 匹配键。"""
    if not name:
        return None
    m = _TS_PAIR.search(name)
    return f"{m.group(1)}_{m.group(2)}" if m else None


def _decode_hdr(s: str) -> str:
    if not s:
        return ""
    out = ""
    try:
        for v, enc in decode_header(s):
            out += v.decode(enc or "utf-8", "ignore") if isinstance(v, bytes) else str(v)
    except Exception:
        return str(s)
    return out


def _msg_body(msg) -> str:
    """取邮件正文(plain + html 去标签),拼成一段纯文本用于正则。"""
    parts = []
    for p in (msg.walk() if msg.is_multipart() else [msg]):
        if p.get_content_type() in ("text/plain", "text/html"):
            try:
                parts.append(p.get_payload(decode=True).decode(
                    p.get_content_charset() or "utf-8", "ignore"))
            except Exception:
                pass
    raw = "\n".join(parts)
    # HTML 里的链接可能含实体 / 标签,顺带产出去标签版一起搜
    return raw + "\n" + html.unescape(re.sub(r"<[^>]+>", " ", raw))


class NotificationMailReader:
    """QQ/通用 IMAP 收件箱读取器。QQ 要求登录后先发 ID 命令,否则判「不安全登录」。"""

    def __init__(self, host, user, password, port=993, mailbox="INBOX"):
        self.host, self.user, self.password = host, user, password
        self.port, self.mailbox = port, mailbox
        self._M = None

    def __enter__(self):
        self.connect(); return self

    def __exit__(self, *a):
        try:
            if self._M:
                self._M.logout()
        except Exception:
            pass

    def connect(self):
        imaplib.Commands.setdefault("ID", ("AUTH",))
        self._M = imaplib.IMAP4_SSL(self.host, self.port)
        self._M.login(self.user, self.password)
        # QQ ID 命令
        try:
            args = ("name", "geo-downloader", "contact", self.user,
                    "version", "1.0", "vendor", "pyclient")
            typ, _ = self._M._simple_command("ID", '("' + '" "'.join(args) + '")')
            self._M._untagged_response(typ, [None], "ID")
        except Exception:
            pass
        self._M.select(self.mailbox, readonly=True)

    def iter_recent_bodies(self, limit=400, from_filter: Optional[str] = None):
        """遍历最近 limit 封邮件,yield (subject, date, body_text)。
        from_filter:只看该发件人(服务端 FROM 搜索,QQ 可靠)。"""
        M = self._M
        if from_filter:
            typ, data = M.search(None, "FROM", from_filter)
        else:
            typ, data = M.search(None, "ALL")
        ids = data[0].split() if (typ == "OK" and data and data[0]) else []
        ids = ids[-limit:]
        # 批量 fetch 提速
        for i in range(0, len(ids), 80):
            seq = b",".join(ids[i:i + 80]).decode()
            typ, items = M.fetch(seq, "(RFC822)")
            for it in (items or []):
                if not isinstance(it, tuple):
                    continue
                try:
                    msg = email.message_from_bytes(it[1])
                except Exception:
                    continue
                yield (_decode_hdr(msg.get("Subject", "")),
                       msg.get("Date", ""), _msg_body(msg))


def extract_all_links(reader: NotificationMailReader, limit=400,
                      from_filter: Optional[str] = None) -> List[Dict]:
    """扫收件箱,返回所有下载链接:
    [{kind:'prisma'|'enmap', url, key, date, subject}, …]。新邮件在后。"""
    out = []
    for subj, date, body in reader.iter_recent_bodies(limit, from_filter):
        for u in dict.fromkeys(_PRISMA_LINK.findall(body)):
            ts = timestamps_from_filename(u)
            out.append({"kind": "prisma", "url": u, "key": ts, "date": date, "subject": subj})
        for u in dict.fromkeys(_FTPS_LINK.findall(body)):
            m = _DIMS_NAME.search(u)
            out.append({"kind": "enmap", "url": u, "key": m.group(0) if m else None,
                        "date": date, "subject": subj})
    return out


# ── 便捷查找接口(自动读配置,失败返回 None,绝不抛到调用方)────────────────────

def find_prisma_zip(timestamps: str, limit=500,
                    config_path: Optional[Path] = None) -> Optional[str]:
    """按 <start>_<stop> 时间戳对,在通知邮箱里找 PRISMA 的 L2D zip 直链。
    返回最新一条匹配链接,找不到/未配置返回 None。"""
    cfg = load_imap_config(config_path)
    if not cfg or not timestamps:
        return None
    try:
        with NotificationMailReader(**cfg) as r:
            hit = None
            for link in extract_all_links(r, limit):
                if link["kind"] == "prisma" and timestamps in link["url"]:
                    hit = link["url"]   # 不 break,取最后(最新)一条
            return hit
    except Exception as e:
        print(f"    [mail] PRISMA 链接查找失败: {e}")
        return None


def find_enmap_ftps(dims_name: Optional[str] = None, order_date: Optional[str] = None,
                    limit=500, config_path: Optional[Path] = None) -> Optional[str]:
    """找 EnMAP 的 FTPS 交付链接。
    优先按 dims_op_oc_oc-en_<id> 精确匹配;dims_name 为空则返回最新一条 EnMAP 链接
    (供「只有一个待处理订单」的场景)。找不到/未配置返回 None。"""
    cfg = load_imap_config(config_path)
    if not cfg:
        return None
    try:
        with NotificationMailReader(**cfg) as r:
            # EnMAP 交付通知来自固定发件人,缩小范围更快更准
            links = [l for l in extract_all_links(r, limit, from_filter="NRSDL-Operations@dlr.de")
                     if l["kind"] == "enmap"]
            if not links:
                links = [l for l in extract_all_links(r, limit) if l["kind"] == "enmap"]
            if dims_name:
                for l in reversed(links):
                    if l["key"] and dims_name in l["key"]:
                        return l["url"]
                return None
            return links[-1]["url"] if links else None
    except Exception as e:
        print(f"    [mail] EnMAP 链接查找失败: {e}")
        return None


def find_enmap_ftps_by_order_id(order_id: str, limit=600,
                                config_path: Optional[Path] = None) -> List[str]:
    """按 EOWEB Order Id 在「Delivery Notice」邮件正文里匹配(邮件含
    'your order with Id = <order_id>'),返回该单的全部 FTPS 链接(一单可能多包)。
    找不到/未配置返回 []。"""
    cfg = load_imap_config(config_path)
    if not cfg or not order_id:
        return []
    try:
        urls = []
        with NotificationMailReader(**cfg) as r:
            for subj, _date, body in r.iter_recent_bodies(
                    limit, from_filter="NRSDL-Operations@dlr.de"):
                if "Deliver" not in subj:
                    continue
                if order_id not in body:
                    continue
                urls.extend(_FTPS_LINK.findall(body))
        return list(dict.fromkeys(urls))   # 去重保序
    except Exception as e:
        print(f"    [mail] EnMAP(by order_id) 查找失败: {e}")
        return []
