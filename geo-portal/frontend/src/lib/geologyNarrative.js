import { DATA_SOURCES } from './stages'

const MODELS = {
  copper: {
    name: '斑岩型铜矿',
    system: '中酸性侵入体及其外围岩浆-热液系统',
    focus: '侵入体边界、环状/放射状裂隙、钾化-绢英岩化-青磐岩化分带和 Cu-Mo-Au 异常组合',
    targetRule: '优先寻找侵入体边界或断裂交汇附近, 且蚀变分带、物探异常和化探异常叠合的部位。',
  },
  gold: {
    name: '浅成低温热液金矿',
    system: '断裂控矿的浅成低温热液系统',
    focus: '控矿断裂、次级裂隙、铁染/硅化/泥化/绢英岩化和 Au-Ag-As-Sb-Hg 异常组合',
    targetRule: '优先寻找构造通道明确、热液蚀变强、物化探异常一致且深度合理的部位。',
  },
  iron: {
    name: 'IOCG 铁氧化物铜金矿',
    system: '铁氧化物-铜-金热液系统',
    focus: '深大断裂、铁氧化物异常、钠钙质蚀变、强磁/重力异常和 Cu-Au-Co-REE 线索',
    targetRule: '优先寻找深大构造附近, 磁重异常叠合并伴随铁氧化物和 Cu-Au-Co 线索的部位。',
  },
  leadzinc: {
    name: 'SEDEX 铅锌矿',
    system: '沉积盆地控矿的层控喷流沉积系统',
    focus: '有利层位、同沉积断裂、硅化/重晶石化/黄铁矿化和 Pb-Zn-Ag-Ba 异常组合',
    targetRule: '优先寻找有利层位内、同沉积断裂附近, 且 Pb-Zn-Ag-Ba 异常和物探响应一致的部位。',
  },
  oilgas: {
    name: '构造-储层控藏(油气/能源)',
    system: '盆地构造-储层控藏成藏系统',
    focus: '构造圈闭(背斜/断块)、盆地与储层边界、断裂封堵性、重磁位场响应和地表形变背景',
    targetRule: '优先寻找构造圈闭完整、储盖组合与储层边界有利、断裂封闭性好且物探响应一致的部位; 蚀变仅作辅助遥感异常, 不作核心门控证据。',
  },
  molybdenum: {
    name: '斑岩型钼(钨钼)矿',
    system: '中酸性侵入体顶部的斑岩-石英细脉热液系统',
    focus: '岩体顶凸与接触带、细脉浸染状石英脉、钾化-绢英岩化分带和 Mo-W-Cu 异常',
    targetRule: '优先寻找侵入体顶部/接触带、脉系密集且蚀变分带与 Mo-W 异常叠合的部位。',
  },
  skarn: {
    name: '矽卡岩型钨锡多金属矿',
    system: '侵入体与碳酸盐岩接触交代的矽卡岩系统',
    focus: '岩体-碳酸盐岩接触带、矽卡岩化(石榴子石-透辉石)、断裂导矿和 W-Sn-Cu-Mo-Bi 异常',
    targetRule: '优先寻找接触带及其层间/断裂扩容部位, 矽卡岩化强且 W-Sn 多金属异常一致的位置。',
  },
  magmatic: {
    name: '岩浆型铜镍(铬铂钒钛)矿',
    system: '基性-超基性岩浆熔离/堆晶成矿系统',
    focus: '镁铁-超镁铁岩体底部与边缘、岩浆通道、强磁/高密度异常和 Ni-Cu-Co-Cr-PGE-V-Ti 线索',
    targetRule: '优先寻找镁铁质岩体底部、岩浆通道收缩部位, 磁重异常与 Ni-Cu-PGE 线索叠合处。',
  },
  pegmatite: {
    name: '伟晶岩/稀有金属花岗岩(锂铍铌钽)',
    system: '高分异花岗岩-伟晶岩稀有金属成矿系统',
    focus: '高分异岩体外接触带、伟晶岩脉群、云英岩化/钠长石化和 Li-Be-Nb-Ta-Rb-Cs 异常',
    targetRule: '优先寻找高分异岩体顶部及外围伟晶岩密集带, 蚀变分带清晰且稀有金属异常富集处。',
  },
  ree: {
    name: '稀土/稀散(碳酸岩/离子吸附型)',
    system: '碱性-碳酸岩岩浆或风化壳离子吸附成矿系统',
    focus: '碱性杂岩/碳酸岩体、断裂控岩、风化壳厚度、放射性/重稀土异常和 REE 组合',
    targetRule: '优先寻找碱性-碳酸岩体及断裂控制部位, 或厚层风化壳内 REE 异常富集区。',
  },
  uranium: {
    name: '砂岩型/热液型铀矿',
    system: '盆地氧化还原界面或断裂控制的铀成矿系统',
    focus: '盆地边缘、层间氧化带/还原过渡面、断裂导矿、放射性异常和 U-Mo-Se-Re 组合',
    targetRule: '优先寻找层间氧化-还原过渡带或断裂与有利层位交汇、放射性异常显著的部位。',
  },
  sedimentary: {
    name: '沉积/层控矿产(非金属与盐类)',
    system: '沉积盆地层控/蒸发岩/风化成矿系统',
    focus: '有利沉积相带与层位、盆地构造格架、岩相边界和层控物化探响应',
    targetRule: '优先圈定有利岩相/层位的展布与厚度稳定区, 结合构造保存条件确定远景段。',
  },
  kimberlite: {
    name: '金伯利岩型金刚石(特殊岩浆矿产)',
    system: '深源金伯利岩/煌斑岩侵位成矿系统',
    focus: '克拉通深大断裂、岩管/岩脉磁异常、指示矿物分散晕和地貌环形构造',
    targetRule: '优先寻找深大断裂交汇的环形/岩管异常, 配合指示矿物分散晕和磁异常定位。',
  },
  comprehensive: {
    name: '多金属/综合找矿(多源证据)',
    system: '多成因叠加的综合成矿背景',
    focus: '区域构造格架、多类蚀变与物化探异常组合、有利层位与岩体边界的综合叠合',
    targetRule: '以多源证据空间叠合为主线, 圈定构造、蚀变、物探与化探异常一致的综合远景区。',
  },
}

const SOURCE_PURPOSE = {
  sentinel2: 'Sentinel-2 用于识别铁染、羟基和地表覆盖差异。',
  landsat8: 'Landsat-8 用于区域岩性、铁染和线性构造解译。',
  sentinel1: 'Sentinel-1/SAR 可辅助识别线性构造、地表粗糙度和形变背景。',
  aster: 'ASTER 的 SWIR/TIR 波段可增强黏土、硅化和热液蚀变信息。',
  dem: 'DEM 用于提取地形线性体、坡度突变、断裂地貌和控矿构造格局。',
  emag2: 'EMAG2 磁场用于约束磁性体、岩体边界或蚀变退磁带。',
  gravity: '重力数据用于识别密度差异、岩体边界和盆地/构造格架。',
  geochem_bg: '化探背景值用于判定元素异常强度、组合和异常晕。',
  mineral_kb: '矿种知识库用于约束成矿模型、蚀变组合和证据权重。',
}

const SOURCE_LABELS = Object.fromEntries(DATA_SOURCES.flatMap((g) => g.items.map((i) => [i.key, i.label])))

const STRUCTURE_RULES = {
  copper: {
    interpretation: '构造证据指向侵入体边界、环状/放射状裂隙和断裂交汇部位对成矿空间具有控制作用; 这些部位解释为岩浆-热液流体上升、侧向扩散和蚀变分带展开的主要通道。',
    metallogeny: '对斑岩型铜矿而言, 构造不是单独成矿证据, 而是限定热液中心、裂隙渗透率和矿化壳体边界的空间框架。',
  },
  gold: {
    interpretation: '构造证据指向线性构造密集带、主断裂与次级裂隙交汇处、弯曲转折或张性释放部位构成主要控矿空间; 这些部位解释为热液上升通道和金银沉淀的有利空间。',
    metallogeny: '对浅成低温热液金矿而言, 断裂活动提供流体通道, 构造扩容部位有利于压力释放、沸腾和 Au-Ag 等成矿物质沉淀。',
  },
  iron: {
    interpretation: '构造证据指向深大断裂、断裂转换部位和磁重异常边界对成矿具有控制作用; 这些构造解释为连通深部热液来源并控制铁氧化物和铜金矿化展布的通道。',
    metallogeny: '对 IOCG 矿床而言, 构造的意义在于约束深源流体通道、氧化还原界面和高磁/高密度异常的成矿位置。',
  },
  leadzinc: {
    interpretation: '构造证据指向盆地边界断裂、同沉积断裂和有利层位展布方向控制矿化空间; 这些构造解释为热卤水运移、喷流通道和沉积中心的主要约束。',
    metallogeny: '对 SEDEX 铅锌矿而言, 构造主要决定成矿流体进入沉积盆地的位置以及 Pb-Zn-Ag-Ba 异常沿层位富集的空间。',
  },
  oilgas: {
    interpretation: '构造证据指向构造圈闭(背斜/断块)、盆地与储层边界、断裂封堵带对油气聚集与保存的控制作用; 这些部位解释为流体运移、圈闭遮挡与油气富集的有利空间。',
    metallogeny: '对油气/能源类目标而言, 构造主要决定圈闭的形成与完整性、储盖组合的边界以及断裂的封堵或输导性质, 是成藏的核心约束; 蚀变仅作辅助遥感异常, 不作核心门控。',
  },
  molybdenum: {
    interpretation: '构造证据指向侵入体顶凸、接触带和脉系密集的断裂裂隙系统对成矿空间的控制; 这些部位解释为含 Mo-W 热液上升与细脉浸染富集的通道。',
    metallogeny: '对斑岩钼(钨钼)矿而言, 构造限定岩体顶部减压裂隙网络、脉系延展方向与蚀变分带边界, 是矿化富集的空间框架。',
  },
  skarn: {
    interpretation: '构造证据指向岩体-碳酸盐岩接触带、层间滑脱和断裂扩容部位控制矽卡岩化与矿化展布; 这些部位解释为成矿流体交代与多金属沉淀的有利空间。',
    metallogeny: '对矽卡岩型钨锡多金属矿而言, 构造决定接触带形态、流体进入碳酸盐岩的通道以及 W-Sn 多金属沿接触带/断裂的富集位置。',
  },
  magmatic: {
    interpretation: '构造证据指向深大断裂、岩浆通道与岩体底部形态控制熔离硫化物的就位; 这些构造解释为镁铁质岩浆上升、贯入与 Ni-Cu-PGE 富集的通道。',
    metallogeny: '对岩浆型铜镍(铬铂)矿而言, 构造主要约束岩浆通道位置、岩体底部凹陷/收缩部位和熔离矿浆聚集的空间。',
  },
  pegmatite: {
    interpretation: '构造证据指向高分异岩体外接触带、张性裂隙和伟晶岩脉群的展布方向控制稀有金属富集; 这些部位解释为残余熔体/挥发分上侵与结晶分异的有利空间。',
    metallogeny: '对伟晶岩/稀有金属矿而言, 构造控制残余熔体的运移与就位、伟晶岩脉群延展和 Li-Be-Nb-Ta 的空间分带。',
  },
  ree: {
    interpretation: '构造证据指向碱性-碳酸岩体的断裂控岩格架, 或风化壳保存的地貌-构造条件控制稀土富集; 这些部位解释为深源流体上升通道或风化壳稳定富集区。',
    metallogeny: '对稀土/稀散矿产而言, 构造约束碱性岩浆-碳酸岩的就位通道, 或控制风化壳厚度与离子吸附型 REE 的保存空间。',
  },
  uranium: {
    interpretation: '构造证据指向盆地边缘断裂、层间氧化-还原过渡面和导矿断裂控制铀的迁移与沉淀; 这些部位解释为含铀流体运移与氧化还原障富集的有利空间。',
    metallogeny: '对铀矿而言, 构造决定盆地构造格架、层间氧化带的发育与展布以及断裂沟通深部还原流体的位置, 是铀富集保存的核心约束。',
  },
  sedimentary: {
    interpretation: '构造证据主要约束有利沉积相带与层位的展布、盆地构造格架和后期保存条件; 构造在此更多体现为层控边界与盆地格架, 而非热液通道。',
    metallogeny: '对沉积/层控非金属矿产而言, 构造决定有利岩相/层位的分布、厚度稳定区与后期改造保存, 是远景段圈定的格架约束。',
  },
  kimberlite: {
    interpretation: '构造证据指向克拉通内深大断裂及其交汇部位控制金伯利岩岩管/岩脉的侵位; 这些部位解释为深源岩浆快速上升与就位的通道。',
    metallogeny: '对金伯利岩型金刚石而言, 构造主要控制深大断裂交汇的岩管群就位位置, 是定位含矿岩体的首要线索。',
  },
  comprehensive: {
    interpretation: '构造证据用于建立区域构造格架, 识别断裂密集带、岩体边界与异常叠合部位作为多源证据的空间纽带; 在未限定单一矿床类型时作为综合约束。',
    metallogeny: '在综合找矿场景下, 构造的作用是统一组织各类证据的空间关系, 圈定构造、蚀变、物化探异常一致的综合远景区。',
  },
}

const SERVICE_ALIASES = {
  'geo-downloader': 'downloader',
  'data-colle': 'datacolle',
  'geo-analyser': 'analyser',
  'geo-stru': 'stru',
  'geo-geophys': 'geophys',
  'geo-geochem': 'geochem',
  'geo-insar': 'insar',
  'geo-model3d': 'model3d',
  'geo-drill': 'drill',
  'geo-reporter': 'reporter',
  'geo-7slow': 'slowvars',
  'geo-exploration': 'exploration',
}

function normService(name) {
  const key = String(name || '').trim().toLowerCase().replaceAll('_', '-')
  return SERVICE_ALIASES[key] || key
}

function groupMap(plan = {}) {
  const out = {}
  ;(plan.execution_plan?.phases || []).forEach((phase) => {
    ;(phase.parallel_groups || []).forEach((g) => {
      const key = normService(g.service)
      out[key] = { ...g, phase: phase.name }
    })
  })
  return out
}

function runContext(run = {}) {
  const plan = run?.plan || run || {}
  const groups = groupMap(plan)
  const roi = plan.roi || {}
  const rationale = plan.decision_rationale || {}
  const downloaderTasks = groups.downloader?.tasks || []
  const sensors = downloaderTasks.map((t) => t.sensor).filter(Boolean)
  return {
    plan,
    stages: run?.stages || {},
    evidencePlan: run?.evidence_plan || {},
    groups,
    roi,
    rationale,
    family: plan.family || '',
    depthBand: rationale.depth_band || groups.model3d?.tasks?.[0]?.depth_band || '',
    weightSummary: rationale.weight_summary || '',
    familyReason: rationale.family_determination || '',
    roiAdjustment: rationale.roi_adjustment || '',
    sensors,
    downloaderTasks,
    existingProducts: roi.existing_products || {},
  }
}

function sentenceList(items = [], limit = 2) {
  return items.filter(Boolean).slice(0, limit).join(' ')
}

function productFacts(products = {}) {
  const yes = Object.entries(products).filter(([, v]) => v).map(([k]) => k.replaceAll('_', '-'))
  const no = Object.entries(products).filter(([, v]) => !v).map(([k]) => k.replaceAll('_', '-'))
  return { yes, no }
}

function modelMismatchLine(f) {
  const label = String(f.mineralLabel || '').toLowerCase()
  if (!f.run.family) return null
  if (f.run.family === 'porphyry' && label.includes('epithermal')) {
    return `项目存在模型分歧: 标注为 ${f.mineralLabel}, 本次 orchestrator family 判定为 ${f.run.family}; 当前靶区解释按浅成低温热液-斑岩过渡可能性处理, 置信度相应下调。`
  }
  return null
}

function mineralKey(current = {}) {
  const raw = `${current.mineral || ''} ${current.mineral_label || ''}`.toLowerCase()
  // 顺序敏感:含"金"的中文词(金刚石/多金属/铂族金属)必须先于 gold 判定,避免误归金。
  // ── 能源 / 油气 ──
  if (raw.includes('oil') || raw.includes('gas') || raw.includes('油') || raw.includes('气') ||
      raw.includes('coal') || raw.includes('煤') || raw.includes('geothermal') || raw.includes('地热') ||
      raw.includes('hydrocarbon') || raw.includes('能源')) return 'oilgas'
  if (raw.includes('uranium') || raw.includes('铀')) return 'uranium'
  // ── 金属:成矿模型族 ──
  if (raw.includes('molybd') || raw.includes('钼')) return 'molybdenum'           // 斑岩钼
  if (raw.includes('tungsten') || raw.includes('钨') || /\btin\b/.test(raw) || raw.includes('锡') ||
      raw.includes('bismuth') || raw.includes('铋') || raw.includes('skarn') || raw.includes('矽卡岩')) return 'skarn'
  if (raw.includes('nickel') || raw.includes('镍') || raw.includes('cobalt') || raw.includes('钴') ||
      raw.includes('chrom') || raw.includes('铬') || raw.includes('platinum') || raw.includes('pge') || raw.includes('铂') ||
      raw.includes('titanium') || raw.includes('钛') || raw.includes('vanadium') || raw.includes('钒')) return 'magmatic'
  if (raw.includes('copper') || raw.includes('铜')) return 'copper'               // 斑岩铜
  if (raw.includes('iron') || raw.includes('铁') || raw.includes('iocg')) return 'iron'
  if (raw.includes('lithium') || raw.includes('锂') || raw.includes('beryllium') || raw.includes('铍') ||
      raw.includes('niobium') || raw.includes('tantalum') || raw.includes('铌') || raw.includes('钽') ||
      raw.includes('rubidium') || raw.includes('cesium') || raw.includes('铷') || raw.includes('铯') ||
      raw.includes('zirconium') || raw.includes('hafnium') || raw.includes('锆') || raw.includes('铪') ||
      raw.includes('pegmatite') || raw.includes('伟晶')) return 'pegmatite'         // 稀有金属伟晶岩
  if (raw.includes('rare_earth') || raw.includes('rare earth') || raw.includes('ree') || raw.includes('稀土') ||
      raw.includes('gallium') || raw.includes('镓') || raw.includes('germanium') || raw.includes('锗') ||
      raw.includes('indium') || raw.includes('铟') || raw.includes('稀散')) return 'ree'
  if (raw.includes('lead') || raw.includes('zinc') || raw.includes('铅') || raw.includes('锌') ||
      raw.includes('sedex') || raw.includes('mvt')) return 'leadzinc'
  if (raw.includes('diamond') || raw.includes('金刚石') || raw.includes('kimberlite') || raw.includes('金伯利') ||
      raw.includes('gemstone') || raw.includes('宝玉石')) return 'kimberlite'        // 先于 gold(含"金")
  // ── 非金属 / 沉积 / 盐类 / 工业矿物 ──
  if (raw.includes('phosphate') || raw.includes('磷') || raw.includes('potash') || raw.includes('钾盐') ||
      raw.includes('salt') || raw.includes('岩盐') || raw.includes('卤水') || raw.includes('brine') ||
      raw.includes('fluorite') || raw.includes('萤石') || raw.includes('barite') || raw.includes('重晶石') ||
      raw.includes('graphite') || raw.includes('石墨') || raw.includes('quartz') || raw.includes('石英') ||
      raw.includes('limestone') || raw.includes('石灰岩') || raw.includes('dolomite') || raw.includes('白云岩') ||
      raw.includes('gypsum') || raw.includes('石膏') || raw.includes('kaolin') || raw.includes('高岭土') ||
      raw.includes('bauxite') || raw.includes('铝土') || raw.includes('manganese') || raw.includes('锰')) return 'sedimentary'
  if (raw.includes('multi') || raw.includes('多金属') || raw.includes('综合')) return 'comprehensive'  // 先于 gold(含"金")
  // ── 贵金属(金银锑汞)→ 浅成低温热液 ──
  if (raw.includes('gold') || raw.includes('金') || raw.includes('silver') || raw.includes('银') ||
      raw.includes('antimony') || raw.includes('锑') || raw.includes('mercury') || raw.includes('汞')) return 'gold'
  return 'comprehensive'   // 默认改为"综合多源",不再误导为金
}

function evidenceByKey(rows, key) {
  return rows.find((r) => r.key === key) || { key, label: key, status: 'pending' }
}

function rowState(row) {
  if (row.status === 'completed' && row.degraded) return 'degraded'
  if (row.status === 'completed' && row.modelDerived) return 'modelDerived'
  if (row.status === 'completed' && row.layerUrl) return 'layer'
  if (row.status === 'completed' && row.archived) return 'archived'
  if (row.status === 'completed' && row.noLayer) return 'noLayer'
  if (row.status === 'completed') return 'completed'
  return row.status || 'pending'
}

function factLine(row) {
  const state = rowState(row)
  const planText = row.weight != null
    ? `编排权重 ${Number(row.weight).toFixed(2)}${row.requiredLevel ? `, 级别 ${row.requiredLevel}` : ''}; `
    : ''
  if (state === 'layer') return `${row.label}证据已形成可叠加图层, 可作为本项目空间证据参与分析。`
  if (state === 'modelDerived') {
    const reason = row.reason || '已作为三维融合输入参与靶点评分'
    const coverage = row.noLayer ? '当前视图尚未展开其代表性栅格, 但模型统计已记录其入模贡献' : '当前已取得可用于三维融合的输入层'
    return `${row.label}证据已进入三维融合模型（${planText}${reason}）; ${coverage}。`
  }
  if (state === 'archived') {
    const reason = row.skipReason || row.reason || 'orchestrator 标记已有历史产物'
    return `${row.label}证据已有产物记录（${reason}）, 当前视图尚未接入代表性可视栅格; 证据链可引用其处理结论, 但空间叠合关系需加载原始产物后复核。`
  }
  if (state === 'noLayer') return `${row.label}证据已完成, 但当前产物未提供可直接叠加的栅格; 这降低的是当前视图的空间可核查性, 不等同于该证据地质意义较低。`
  if (state === 'completed') return `${row.label}证据已完成, ${planText}当前处于产物待解析状态; 需要接入代表性图层或产物摘要后再判定空间贡献强弱。`
  if (state === 'degraded') return `${row.label}证据已降级完成: ${row.error || '真实服务失败'}, 只能作为低置信缺失线索。`
  if (state === 'failed') return `${row.label}证据失败: ${row.error || '未知原因'}, 当前不能支撑靶区结论。`
  if (state === 'skipped') return `${row.label}证据本次跳过, 不参与当前证据链。`
  if (state === 'running') return `${row.label}证据正在生成, 结论仍未闭合。`
  return `${row.label}证据尚未运行, 不能作为当前项目事实。`
}

function structureFactLine(row, f, kind = 'structure') {
  const state = rowState(row)
  const modelRule = STRUCTURE_RULES[mineralKey(f.current)] || STRUCTURE_RULES.gold
  const struGroup = f.run.groups.stru || {}
  const insarGroup = f.run.groups.insar || {}
  const tectonic = f.run.roi.tectonic_setting
  const area = f.run.roi.area_km2
  if (kind === 'insar') {
    const reason = insarGroup.reason || 'InSAR 形变监测'
    if (state === 'layer') return `${row.label}图层已接入; 本次编排说明为“${reason}”。当前解释为 AOI 内断裂带活动背景和破碎带响应的辅助证据, 与构造图层共同约束热液通道的活动性。`
    if (state === 'modelDerived') return `${row.label}证据已作为三维融合输入参与靶点评分; 本次解释为 AOI 内断裂活动背景和破碎带响应的辅助约束, 用于校验构造通道是否具备近期活动或应力释放线索。`
    if (state === 'archived') return `${row.label}已有产物记录（${insarGroup.skip_reason || row.skipReason || '历史形变产物'}）; 当前解释为构造活动背景线索, 其成矿意义取决于与断裂、蚀变和物化探异常的叠合程度。`
    return factLine(row)
  }
  if (state === 'layer' || state === 'modelDerived') {
    const runReason = struGroup.reason ? `本次 geo-stru 任务说明为“${struGroup.reason}”。` : ''
    const areaText = area ? `AOI 面积约 ${area} km²` : '当前 AOI'
    const tectText = tectonic ? `, 区域构造背景为${tectonic}` : ''
    const sourceText = state === 'layer' ? `${row.label}图层已接入` : `${row.label}证据已作为三维融合输入参与靶点评分`
    return `${sourceText}; ${areaText}${tectText}。${runReason} 本次推理结论为: 线性构造密度、距断裂距离和断裂交汇部位共同限定了主要控矿空间, ${modelRule.interpretation}`
  }
  if (state === 'archived') {
    return `${row.label}已有产物记录（${struGroup.skip_reason || row.skipReason || '历史构造产物'}）; 当前证据链已采用其断裂/线性构造解译结论, 但断裂走向、交汇部位与异常叠合的细节尚未在当前视图展开。`
  }
  return factLine(row)
}

function structureMetallogenyLine(f) {
  const modelRule = STRUCTURE_RULES[mineralKey(f.current)] || STRUCTURE_RULES.gold
  const crossEvidence = [
    f.analyser.status === 'completed' ? '蚀变异常' : null,
    f.geophys.status === 'completed' ? '物探异常' : null,
    f.geochem.status === 'completed' ? '化探异常' : null,
  ].filter(Boolean)
  const suffix = crossEvidence.length
    ? `当前证据链将构造带与${crossEvidence.join('、')}的叠合区解释为靶区优先区。`
    : '由于其他证据尚未闭合, 当前结论仅把构造带解释为找矿方向约束, 尚未上升为靶区级证据。'
  const depth = f.run.depthBand ? `三维建模深度带为 ${f.run.depthBand}, 构造证据被解释为该深度范围内热液通道连续性的上部约束。` : ''
  return `${modelRule.metallogeny} ${depth} ${suffix}`.replace(/\s+/g, ' ')
}

function confidence(rows) {
  const completed = rows.filter((r) => r.status === 'completed' && !r.degraded).length
  const degraded = rows.filter((r) => r.degraded).length
  const failed = rows.filter((r) => r.status === 'failed').length
  if (completed >= 4 && failed === 0 && degraded === 0) return '较高'
  if (completed >= 3 && failed <= 1) return '中等'
  if (completed >= 2) return '中低'
  return '低'
}

function model3dLine(f) {
  if (f.topTarget) {
    return `当前首位/选中靶点评分约 ${Number(f.topTarget.score || 0).toFixed(2)}, 深度约 ${f.topTarget.depth_m || '-'} m。`
  }
  const modelStage = f.run.stages?.model3d || {}
  const modelGroup = f.run.groups.model3d || {}
  if (modelStage.status === 'completed' || modelGroup.skip) {
    const reason = modelGroup.skip_reason || modelGroup.reason || '已有三维建模产物'
    return `三维建模已有产物记录（${reason}）, 但当前视图尚未加载靶点坐标、评分和深度详情; 因此只能确认模型环节已闭合, 不能在本视图中给出具体靶点参数。`
  }
  if (modelStage.status === 'running') return '三维建模正在运行, 靶点坐标、评分和深度尚未闭合。'
  if (modelStage.status === 'failed') return `三维建模失败${modelStage.error ? `: ${modelStage.error}` : ''}, 当前不能形成三维靶区结论。`
  return '当前尚未形成三维靶点产物, 靶区推断仍停留在二维证据综合阶段。'
}

function targetConfidenceLine(f) {
  if (f.topTarget) {
    return `靶区置信度评估为 ${f.confidence}; 该结论由二维证据与三维靶点参数共同支撑, 同时受缺失或未接入证据约束。`
  }
  const hasModelRecord = f.run.stages?.model3d?.status === 'completed' || f.run.groups.model3d?.skip
  if (hasModelRecord) {
    return `二维证据一致性评估为 ${f.confidence}; 但当前未加载三维靶点详情, 因此这里评估的是证据基础可靠性, 不是具体靶点空间置信度。`
  }
  return `二维证据一致性评估为 ${f.confidence}; 三维模型尚未形成靶点产物, 靶区空间置信度仍未闭合。`
}

export function buildProjectEvidenceFacts({ current, evidenceRows = [], selectedSources = [], model3d = {}, target = null, run = null }) {
  const model = MODELS[mineralKey(current)] || MODELS.gold
  const bbox = current?.aoi_bbox
  const runInfo = runContext(run || {})
  const areaName = current?.name || '当前项目'
  const sourceNames = selectedSources.map((k) => SOURCE_LABELS[k] || k)
  const sourcePurposes = selectedSources.map((k) => SOURCE_PURPOSE[k]).filter(Boolean)
  const rows = evidenceRows.map((r) => ({ ...r, state: rowState(r) }))
  const topTarget = target || model3d?.targets?.[0] || null
  return {
    current,
    run: runInfo,
    model,
    areaName,
    mineralLabel: current?.mineral_label || current?.mineral || '未指定矿种',
    aoi: bbox?.length === 4 ? bbox.map((n) => Number(n).toFixed(4)).join(', ') : '未提供 bbox',
    kmlName: current?.kml_name || '样例/未上传 KML',
    sourceNames,
    sourcePurposes,
    rows,
    analyser: evidenceByKey(rows, 'analyser'),
    stru: evidenceByKey(rows, 'stru'),
    geophys: evidenceByKey(rows, 'geophys'),
    geochem: evidenceByKey(rows, 'geochem'),
    insar: evidenceByKey(rows, 'insar'),
    topTarget,
    targetCount: model3d?.targets?.length || 0,
    confidence: confidence(rows),
  }
}

export function buildProjectNarrative(input) {
  const f = buildProjectEvidenceFacts(input)
  const targetText = model3dLine(f)

  const uncertainty = f.rows
    .filter((r) => r.status === 'failed' || r.degraded || r.status === 'skipped')
    .map((r) => `${r.label}: ${r.error || r.relation || '证据不足'}`)
  const mismatch = modelMismatchLine(f)
  if (mismatch) uncertainty.unshift(mismatch)
  const productState = productFacts(f.run.existingProducts)
  const dataTaskLine = f.run.downloaderTasks.length
    ? `实跑编排要求下载/使用 ${f.run.sensors.join('、')}; 其中 ${sentenceList(f.run.downloaderTasks.map((t) => t.reason), 2)}`
    : null
  const modelDecisionLine = [
    f.run.familyReason ? `成因族判定: ${f.run.familyReason}。` : null,
    f.run.weightSummary ? `权重设置: ${f.run.weightSummary}。` : null,
    f.run.evidencePlan?.rationale ? `证据编排: ${f.run.evidencePlan.rationale}` : null,
    f.run.depthBand ? `目标深度带: ${f.run.depthBand}。` : null,
  ].filter(Boolean).join(' ')

  return [
    {
      key: 'project',
      title: '项目背景',
      weight: 'core',
      lines: [
        `${f.areaName} 矿种标注为 ${f.mineralLabel}; 当前证据链按 ${f.model.name} 组织, KML 为 ${f.kmlName}。`,
        `AOI bbox 为 ${f.aoi}${f.run.roi.area_km2 ? `, 面积约 ${f.run.roi.area_km2} km²` : ''}${f.run.roi.tectonic_setting ? `, 构造背景为${f.run.roi.tectonic_setting}` : ''}。`,
        modelDecisionLine || `模型关注: ${f.model.focus}。`,
      ],
    },
    {
      key: 'data',
      title: '数据解译',
      weight: 'normal',
      lines: [
        dataTaskLine || (f.sourceNames.length ? `本次选择数据源: ${f.sourceNames.join('、')}。` : '本次尚未形成完整数据源组合。'),
        f.run.roiAdjustment ? `区域修正: ${f.run.roiAdjustment}。` : (f.sourcePurposes.length ? f.sourcePurposes.join(' ') : '数据源不足会限制遥感、构造和物化探之间的交叉验证。'),
        productState.yes.length ? `已有产物包括 ${productState.yes.slice(0, 6).join('、')}; 当前仍缺失或未接入的产物包括 ${productState.no.slice(0, 4).join('、') || '无'}。` : '尚未读取到已有产物清单。',
      ],
    },
    {
      key: 'structure',
      title: '构造证据',
      weight: f.stru.status === 'completed' && !f.stru.degraded ? 'strong' : 'risk',
      lines: [
        structureFactLine(f.stru, f),
        structureFactLine(f.insar, f, 'insar'),
        structureMetallogenyLine(f),
        f.stru.status === 'completed' && !f.stru.degraded
          ? `因此, 构造证据在当前证据链中的角色是圈定 ${f.model.name} 的热液运移通道、构造扩容部位和异常叠合优先区。`
          : '构造/形变证据不足时, 不能直接声称已识别控矿断裂或运动趋势。',
      ],
    },
    {
      key: 'alteration',
      title: '蚀变证据',
      weight: f.analyser.status === 'completed' && !f.analyser.degraded ? 'strong' : 'risk',
      lines: [
        factLine(f.analyser),
        f.run.groups.analyser?.reason ? `本次蚀变任务说明为“${f.run.groups.analyser.reason}”; 当前解释把蚀变异常视为沿构造通道发育的热液活动响应, 并与 ${f.model.focus} 建立对应关系。` : null,
        f.analyser.status === 'completed' && !f.analyser.degraded
          ? `蚀变证据可用于验证 ${f.model.name} 所需的热液活动和蚀变分带。`
          : '蚀变证据缺失会削弱对热液中心、近矿蚀变和矿床成因关系的判断。',
      ].filter(Boolean),
    },
    {
      key: 'geo',
      title: '物化探约束',
      weight: f.geophys.status === 'completed' || f.geochem.status === 'completed' ? 'strong' : 'normal',
      lines: [
        factLine(f.geophys),
        factLine(f.geochem),
        f.run.groups.geophys?.reason ? `本次物探任务说明为“${f.run.groups.geophys.reason}”; 当前解释把磁/重异常作为岩体边界、破碎带或深部通道的间接约束, 并与 AOI 构造背景和目标深度带共同判读。` : null,
        '物探和化探只有与构造、蚀变同向叠合时, 才能从异常线索上升为靶区证据。',
      ].filter(Boolean),
    },
    {
      key: 'target',
      title: '靶区推导',
      weight: f.confidence === '较高' || f.confidence === '中等' ? 'strong' : 'risk',
      lines: [
        f.model.targetRule,
        modelDecisionLine || null,
        targetText,
        targetConfidenceLine(f),
      ].filter(Boolean),
    },
    {
      key: 'risk',
      title: '不确定性',
      weight: uncertainty.length ? 'risk' : 'normal',
      lines: uncertainty.length
        ? uncertainty
        : ['当前没有记录失败、跳过或降级证据; 主要剩余不确定性来自原始产物空间叠合细节在当前视图中的展开程度。'],
    },
  ]
}

export function buildProjectSummary(input) {
  const sections = buildProjectNarrative(input)
  const target = sections.find((s) => s.key === 'target')
  const risk = sections.find((s) => s.key === 'risk')
  return [
    target?.lines?.[2],
    risk?.weight === 'risk' ? `主要风险: ${risk.lines.slice(0, 2).join('；')}` : null,
  ].filter(Boolean).join(' ')
}

export function buildGeologyNarrative(input) {
  const sections = buildProjectNarrative(input)
  return {
    modelName: buildProjectEvidenceFacts(input).model.name,
    sections,
    thesis: sections.find((s) => s.key === 'project')?.lines?.[0] || '',
    uncertainty: sections.find((s) => s.key === 'risk')?.lines || [],
  }
}

export function flattenNarrative(narrative) {
  if (narrative?.sections) return narrative.sections.map((s) => [s.title, s.lines])
  return []
}
