# downloader package

# asf_search CMR 搜索超时：默认 30s 在代理/高延迟环境下不够用，统一提高到 90s
try:
    import asf_search.constants
    asf_search.constants.INTERNAL.CMR_TIMEOUT = 90
except Exception:
    pass
