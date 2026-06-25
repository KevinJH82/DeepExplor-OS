"""矿种知识库 — 矿种→成矿类型→指示元素→物探方法的映射"""

# ============================================================
# 矿种 → 成矿类型 → 关键要素
# ============================================================

MINERAL_KNOWLEDGE = {
    "铜": {
        "metallogenic_types": [
            {
                "name": "斑岩型铜(钼)矿",
                "tectonic_setting": "汇聚板块边缘、岛弧、陆缘弧",
                "host_rocks": "中酸性斑岩体(花岗闪长斑岩、石英二长斑岩等)",
                "alteration": "钾化→绢英岩化→泥化→青磐岩化(分带)",
                "key_elements": ["Cu", "Mo", "Au", "Ag", "Re", "Pb", "Zn", "As", "Sb"],
                "element_association": "Cu-Mo ± Au (核部) → Pb-Zn-Ag-Au (外围)",
                "geophysical_methods": ["磁法(圈定岩体)", "IP/激电(硫化物)", "重力(隐伏岩体)", "MT(深部结构)"],
                "geophysical_anomalies": "中酸性岩体→低磁；硫化物→高极化；钾化带→低阻",
            },
            {
                "name": "矽卡岩型铜矿",
                "tectonic_setting": "中酸性岩体与碳酸盐岩接触带",
                "host_rocks": "矽卡岩(石榴子石、透辉石等)",
                "alteration": "矽卡岩化→绿帘石化→碳酸盐化",
                "key_elements": ["Cu", "Fe", "Au", "Ag", "Mo", "Zn", "Pb", "Co"],
                "element_association": "Cu-Fe-Au ± Mo (近接触带)",
                "geophysical_methods": ["磁法(磁性矿物+岩体)", "重力(接触带)", "IP(硫化物)", "CSAMT"],
                "geophysical_anomalies": "接触带→重磁梯度带；硫化物富集→高极化+低阻",
            },
            {
                "name": "火山块状硫化物型(VMS)铜矿",
                "tectonic_setting": "海底火山活动带、裂谷环境",
                "host_rocks": "海相火山岩(玄武岩-流纹岩)",
                "alteration": "绿泥石化→绢云母化→硅化",
                "key_elements": ["Cu", "Zn", "Pb", "Au", "Ag", "S", "Ba", "Mn"],
                "element_association": "Cu (核)→ Zn-Pb (外)→ Ba-Mn (远缘)",
                "geophysical_methods": ["IP/激电(块状硫化物)", "电磁法(导体)", "磁法(磁黄铁矿)"],
                "geophysical_anomalies": "块状硫化物→高极化+低阻+磁异常",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 27, "unit": "ppm"},
        "georoc_rock_types": ["Andesite", "Dacite", "Granodiorite", "Diorite", "Rhyolite"],
    },

    "金": {
        "metallogenic_types": [
            {
                "name": "造山型金矿",
                "tectonic_setting": "汇聚板块边缘、剪切带、绿岩带",
                "host_rocks": "绿片岩相-角闪岩相变质岩，剪切带",
                "alteration": "硅化→绢云母化→碳酸盐化→黄铁矿化",
                "key_elements": ["Au", "As", "Sb", "Hg", "Ag", "W", "Bi", "Te", "Pb"],
                "element_association": "Au-As-Sb-Hg (前缘晕)→ Au-Ag-W-Bi (矿体)",
                "geophysical_methods": ["磁法(构造格架)", "IP(硫化物)", "重力(深部结构)"],
                "geophysical_anomalies": "含金石英脉→高阻；硫化物→弱极化异常",
            },
            {
                "name": "浅成低温热液型金矿",
                "tectonic_setting": "火山弧、破火山口",
                "host_rocks": "火山岩(安山岩-流纹岩)、火山角砾岩",
                "alteration": "硅化(硅帽)→高级泥化→绢云母化",
                "key_elements": ["Au", "Ag", "As", "Sb", "Hg", "Tl", "Se", "Te"],
                "element_association": "高硫化型: Au-Cu-As; 低硫化型: Au-Ag-As-Sb-Hg",
                "geophysical_methods": ["磁法(火山机构)", "IP(硫化物)", "CSAMT"],
                "geophysical_anomalies": "硅帽→高阻异常；隐爆角砾岩→环状磁异常",
            },
            {
                "name": "卡林型金矿",
                "tectonic_setting": "被动陆缘碳酸盐岩台地",
                "host_rocks": "碳酸盐岩、钙质碎屑岩",
                "alteration": "脱钙化→硅化→黄铁矿化(细微浸染状)",
                "key_elements": ["Au", "As", "Sb", "Hg", "Tl", "Ba", "Zn", "Pb"],
                "element_association": "Au-As-Sb-Hg-Tl (无明显分带)",
                "geophysical_methods": ["重力(基底结构)", "磁法(深部构造)", "CSAMT(地层)"],
                "geophysical_anomalies": "物探异常不明显(难探测类型)",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 0.0015, "unit": "ppm"},
        "georoc_rock_types": ["Granite", "Andesite", "Basalt", "Rhyolite", "Dacite"],
    },

    "锂": {
        "metallogenic_types": [
            {
                "name": "伟晶岩型锂矿",
                "tectonic_setting": "造山带、古老克拉通边缘",
                "host_rocks": "花岗伟晶岩脉群",
                "alteration": "钠长石化→锂辉石化→锂云母化→电气石化",
                "key_elements": ["Li", "Be", "Nb", "Ta", "Cs", "Rb", "Sn", "B", "F"],
                "element_association": "Li-Be-Nb-Ta ± Cs-Rb (LCT 型伟晶岩)",
                "geophysical_methods": ["磁法(伟晶岩→弱磁)", "放射性(含K伟晶岩→K异常)", "重力"],
                "geophysical_anomalies": "伟晶岩→通常低磁+局部放射性异常",
            },
            {
                "name": "盐湖卤水型锂矿",
                "tectonic_setting": "干旱区内陆盆地(高原盐湖)",
                "host_rocks": "第四纪盐湖沉积/卤水层",
                "alteration": "无热液蚀变(蒸发沉积成因)",
                "key_elements": ["Li", "K", "B", "Mg", "Na", "Br", "I"],
                "element_association": "Li-K-B-Mg",
                "geophysical_methods": ["重力(盆地基底)", "MT(卤水层深度)", "地震(盆地结构)"],
                "geophysical_anomalies": "盆地→重力低；卤水层→低阻层(MT)",
            },
            {
                "name": "黏土型锂矿",
                "tectonic_setting": "稳定陆块、沉积盆地",
                "host_rocks": "含锂黏土岩/铝土矿层",
                "alteration": "风化→黏土化",
                "key_elements": ["Li", "Al", "Si", "Ga", "REE"],
                "element_association": "Li-Al-Ga",
                "geophysical_methods": ["放射性(黏土→低K/U/Th)", "IP"],
                "geophysical_anomalies": "物探异常不显著",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 17, "unit": "ppm"},
        "georoc_rock_types": ["Pegmatite", "Granite", "Leucogranite", "S-type granite"],
    },

    "铅锌": {
        "metallogenic_types": [
            {
                "name": "SEDEX 型铅锌矿",
                "tectonic_setting": "大陆裂谷、被动陆缘盆地",
                "host_rocks": "碳质页岩、粉砂岩、碳酸盐岩",
                "alteration": "硅化→电气石化→碳酸盐化",
                "key_elements": ["Pb", "Zn", "Ag", "Ba", "Mn", "Fe", "Cu", "Cd"],
                "element_association": "Pb-Zn-Ag ± Ba (Fe-Mn 帽)",
                "geophysical_methods": ["重力(盆地基底)", "IP(层状硫化物)", "MT(盆地深部)"],
                "geophysical_anomalies": "硫化物层→低阻+高极化；重力→层控特征",
            },
            {
                "name": "MVT 型铅锌矿",
                "tectonic_setting": "前陆盆地、台地碳酸盐岩",
                "host_rocks": "白云岩/灰岩(角砾岩化)",
                "alteration": "白云岩化→方解石化→黄铁矿化",
                "key_elements": ["Pb", "Zn", "Cd", "Ge", "Ga", "Ba", "F"],
                "element_association": "Pb-Zn-Cd-Ge-Ga",
                "geophysical_methods": ["IP(硫化物)", "重力(角砾岩带)", "CSAMT"],
                "geophysical_anomalies": "角砾岩带→重力低；硫化物→弱极化异常",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": {"Pb": 11, "Zn": 72}, "unit": "ppm"},
        "georoc_rock_types": ["Carbonate_rock", "Shale", "Sandstone"],
    },

    "钨锡": {
        "metallogenic_types": [
            {
                "name": "石英脉型/云英岩型钨锡矿",
                "tectonic_setting": "碰撞造山带、S型花岗岩区",
                "host_rocks": "高分异S型花岗岩+围岩",
                "alteration": "云英岩化→硅化→电气石化→萤石化",
                "key_elements": ["W", "Sn", "Mo", "Bi", "Be", "F", "Li", "Nb", "Ta"],
                "element_association": "W-Sn-Mo-Bi ± Be-Li-F (高温元素组合)",
                "geophysical_methods": ["磁法(岩体边界)", "重力(隐伏花岗岩)", "放射性(含K/Th花岗岩)"],
                "geophysical_anomalies": "高分异花岗岩→高Th/U放射性+低磁",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": {"W": 1.0, "Sn": 1.7}, "unit": "ppm"},
        "georoc_rock_types": ["Granite", "Pegmatite", "Leucogranite", "S-type granite"],
    },

    "稀土": {
        "metallogenic_types": [
            {
                "name": "碳酸岩型稀土矿",
                "tectonic_setting": "裂谷环境、古老克拉通",
                "host_rocks": "碳酸岩-碱性岩杂岩体",
                "alteration": "钠长石化→霓石化→萤石化→重晶石化",
                "key_elements": ["REE", "Nb", "Th", "U", "Ba", "Sr", "F", "P"],
                "element_association": "LREE-Ba-Sr-Nb (碳酸岩) 或 HREE-U-Th (碱性花岗岩)",
                "geophysical_methods": ["放射性(Th/U异常)", "磁法(磁性矿物)", "重力(碳酸岩体)"],
                "geophysical_anomalies": "碳酸岩→强放射性+局部磁异常",
            },
            {
                "name": "离子吸附型稀土矿",
                "tectonic_setting": "风化壳发育区(花岗岩/火山岩区)",
                "host_rocks": "风化壳(花岗岩/火山岩风化产物)",
                "alteration": "风化淋滤→黏土化",
                "key_elements": ["REE", "Y", "La", "Ce", "Nd"],
                "element_association": "LREE型(花岗岩风化壳) 或 HREE型(火山岩风化壳)",
                "geophysical_methods": ["放射性(REE 伴生 Th/U)", "DEM(风化壳分布)"],
                "geophysical_anomalies": "母岩放射性异常+风化壳地貌(DEM解释)",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": {"La": 30, "Ce": 60, "Y": 30}, "unit": "ppm"},
        "georoc_rock_types": ["Carbonatite", "Syenite", "Alkali_granite", "Nepheline_syenite"],
    },

    "石油": {
        "metallogenic_types": [
            {
                "name": "裂谷盆地油气成藏",
                "tectonic_setting": "大陆裂谷、拗拉槽（如渤海湾盆地、松辽盆地）",
                "host_rocks": "烃源岩: 湖相暗色泥页岩；储层: 三角洲/河流相砂岩；盖层: 湖相泥岩/膏盐岩",
                "alteration": "成岩作用（压实→胶结→溶蚀），有机质热演化（Ro 0.5%-1.3% 生油窗）",
                "key_elements": ["TOC", "Ro", "S1", "S2", "HI", "OIP", "孔隙度", "渗透率", "地层压力"],
                "element_association": "生油岩: TOC>2% + S1+S2>6 mg/g；储层: 孔隙度>10% + 渗透率>1mD",
                "geophysical_methods": ["二维地震", "三维地震(构造+储层反演)", "重力(盆地基底)", "磁法(基底深度)", "MT(深部电性结构)", "测井(伽马/电阻率/声波)"],
                "geophysical_anomalies": "背斜/断块/岩性圈闭→地震反射同相轴闭合；烃类→低电阻率(测井)、低频异常(地震)",
            },
            {
                "name": "前陆盆地油气成藏",
                "tectonic_setting": "造山带前缘逆冲推覆带（如塔里木库车、川西）",
                "host_rocks": "烃源岩: 海陆交互相页岩/煤系；储层: 冲积扇/辫状河砂岩；盖层: 膏盐岩/泥岩",
                "alteration": "构造裂缝→次生溶蚀改善储层；有机质热演化（Ro 0.6%-2.0%）",
                "key_elements": ["TOC", "Ro", "S1", "S2", "孔隙度", "裂缝密度", "膏盐层厚度", "构造圈闭面积"],
                "element_association": "煤成气: TOC>3%+Ro>1.3%；致密砂岩气: 孔隙度<10%+裂缝发育",
                "geophysical_methods": ["三维地震(山地)", "重力(基底深度)", "磁法(磁性基底)", "MT(逆冲构造电性结构)", "测井"],
                "geophysical_anomalies": "逆冲构造→地震速度差异大；膏盐层→低密度(重力低)+低电阻率",
            },
            {
                "name": "被动陆缘盆地油气成藏",
                "tectonic_setting": "被动大陆边缘（如南海北部珠江口盆地）",
                "host_rocks": "烃源岩: 海相页岩/泥灰岩；储层: 三角洲/海底扇/碳酸盐岩礁滩；盖层: 海相页岩",
                "alteration": "碳酸盐岩: 白云岩化→溶蚀孔洞发育；有机质热演化（Ro 0.5%-1.5%）",
                "key_elements": ["TOC", "Ro", "HI", "孔隙度", "白云岩化程度", "生物礁发育", "层序地层"],
                "element_association": "湖相→海相过渡；深层→高温高压→凝析油/天然气",
                "geophysical_methods": ["三维地震(构造+属性+反演)", "AVO分析", "重力(基底)", "磁法(基底埋深)", "CSEM(海洋电磁)"],
                "geophysical_anomalies": "礁滩→丘状地震反射；含气砂岩→AVO III类异常(亮点)；油气→高电阻率(CSEM)",
            },
            {
                "name": "页岩油气（非常规）",
                "tectonic_setting": "稳定克拉通内坳陷或前陆盆地深坳陷",
                "host_rocks": "富有机质页岩/泥岩（既是烃源岩也是储层）",
                "alteration": "有机质成熟度（Ro 1.0%-3.5% 生气窗）；脆性矿物（石英+方解石>40%）控制可压裂性",
                "key_elements": ["TOC", "Ro", "脆性指数", "孔隙度", "含气量", "地层压力", "页岩厚度", "埋深"],
                "element_association": "TOC>2% + Ro>1.0% + 脆性矿物>40% + 厚度>30m",
                "geophysical_methods": ["三维地震(甜点预测)", "微地震监测(压裂)", "测井(ECS/核磁)", "重力(区域)"],
                "geophysical_anomalies": "页岩→低伽马(高TOC段除外)、高电阻率(成熟页岩)；脆性段→低泊松比+高杨氏模量",
            },
        ],
        "global_geochemical_background": {"note": "石油: 常规可采资源量与盆地类型相关，非元素背景值能衡量", "unit": "—"},
        "georoc_rock_types": ["Shale", "Sandstone", "Carbonate_rock", "Mudstone"],
        "exploration_indicators": ["烃源岩", "储层", "盖层", "圈闭", "运移", "保存"],
        "six_elements": [
            {"element": "烃源岩", "key_params": "TOC, Ro, 厚度, 分布面积", "description": "有效烃源岩: TOC>1%(湖相)>0.5%(海相), 成熟度 Ro 0.5%-2.0%"},
            {"element": "储层", "key_params": "孔隙度, 渗透率, 厚度, 分布", "description": "碎屑岩: φ>10%, K>1mD; 碳酸盐岩: φ>5%(缝洞型)"},
            {"element": "盖层", "key_params": "岩性, 厚度, 突破压力", "description": "膏盐岩>厚层泥岩>致密碳酸盐岩"},
            {"element": "圈闭", "key_params": "类型, 面积, 闭合幅度", "description": "构造圈闭(背斜/断块) + 地层岩性圈闭"},
            {"element": "运移", "key_params": "输导体系(断裂/不整合/砂体)", "description": "烃源岩→圈闭的空间配置关系"},
            {"element": "保存", "key_params": "构造活动期次, 盖层完整性", "description": "晚期构造运动破坏程度"},
        ],
    },
    "铁": {
        "metallogenic_types": [
            {
                "name": "BIF 条带状铁建造",
                "tectonic_setting": "古老克拉通(太古宙-元古宙)",
                "host_rocks": "条带状铁建造(燧石-赤铁矿/磁铁矿互层)",
                "alteration": "无显著热液蚀变(沉积-变质成因)",
                "key_elements": ["Fe", "Si", "Mn", "P", "S"],
                "element_association": "Fe-Si ± Mn (贫其他金属)",
                "geophysical_methods": ["磁法(强磁异常)", "重力(高密度层)"],
                "geophysical_anomalies": "磁铁矿→强磁异常(数千nT以上)+重力高",
            },
            {
                "name": "矽卡岩型铁矿",
                "tectonic_setting": "中基性岩体与碳酸盐岩接触带",
                "host_rocks": "矽卡岩(磁铁矿+石榴子石+透辉石)",
                "alteration": "矽卡岩化(钠化→方柱石化→磁铁矿化)",
                "key_elements": ["Fe", "Cu", "Co", "Au", "S"],
                "element_association": "Fe-Cu-Co ± Au",
                "geophysical_methods": ["磁法(强磁异常)", "重力(高密度)"],
                "geophysical_anomalies": "磁铁矿→极强磁异常(数千~上万nT)",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 50000, "unit": "ppm"},
        "georoc_rock_types": ["Basalt", "Gabbro", "Diorite", "Granodiorite"],
    },

    "铀": {
        "metallogenic_types": [
            {
                "name": "砂岩型铀矿",
                "tectonic_setting": "中生代盆地(鄂尔多斯、二连、吐哈)",
                "host_rocks": "疏松砂岩(河道/辫状河相)",
                "alteration": "铀矿化与氧化-还原过渡带(卷状体)",
                "key_elements": ["U", "V", "Mo", "Se", "Re", "REE"],
                "element_association": "U-V-Mo(卷状体前锋)",
                "geophysical_methods": ["放射性(γ能谱)", "磁法(基底)", "重力(盆地)", "电法(氧化-还原带)"],
                "geophysical_anomalies": "铀矿体→高γ异常；氧化带→高阻；还原带→低阻",
            },
            {
                "name": "花岗岩型铀矿",
                "tectonic_setting": "产铀花岗岩体(华南)",
                "host_rocks": "花岗岩体内部及外接触带",
                "alteration": "钠长石化→水云母化→萤石化→硅化",
                "key_elements": ["U", "Th", "Mo", "Pb", "Zn", "Cu", "F"],
                "element_association": "U-Mo-F(热液型)",
                "geophysical_methods": ["放射性(γ能谱+氡气)", "磁法(岩体圈定)", "重力"],
                "geophysical_anomalies": "铀矿化→高γ+高氡；花岗岩体→低磁",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 1.7, "unit": "ppm"},
        "georoc_rock_types": ["Granite", "Rhyolite", "Sandstone", "Pegmatite"],
    },

    "锰": {
        "metallogenic_types": [
            {
                "name": "沉积型锰矿",
                "tectonic_setting": "被动陆缘盆地、裂谷盆地",
                "host_rocks": "含锰碳酸盐岩/硅质岩/碎屑岩",
                "alteration": "氧化富集(风化型)或成岩改造",
                "key_elements": ["Mn", "Fe", "P", "Si", "Al", "Ca"],
                "element_association": "Mn-Fe(负相关)±P",
                "geophysical_methods": ["磁法(含锰层弱磁)", "重力"],
                "geophysical_anomalies": "锰矿层→弱磁异常",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 775, "unit": "ppm"},
        "georoc_rock_types": ["Carbonate_rock", "Shale", "Chert"],
    },

    "铬": {
        "metallogenic_types": [
            {
                "name": "豆荚状铬铁矿(蛇绿岩型)",
                "tectonic_setting": "板块缝合带、蛇绿岩带(西藏、新疆)",
                "host_rocks": "超基性岩(方辉橄榄岩、纯橄岩)",
                "alteration": "蛇纹石化",
                "key_elements": ["Cr", "Fe", "Mg", "Al", "Ni", "Co", "Pt", "Os"],
                "element_association": "Cr-Fe-Mg ± PGE",
                "geophysical_methods": ["重力(高密度超基性岩)", "磁法(蛇纹石化→弱磁)", "电法"],
                "geophysical_anomalies": "超基性岩体→重力高+弱磁(蛇纹石化后)",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 92, "unit": "ppm"},
        "georoc_rock_types": ["Peridotite", "Dunite", "Harzburgite", "Pyroxenite"],
    },

    "镍": {
        "metallogenic_types": [
            {
                "name": "岩浆熔离硫化镍矿",
                "tectonic_setting": "克拉通边缘、造山带基性-超基性岩体",
                "host_rocks": "基性-超基性侵入体(苏长岩、橄榄岩)",
                "alteration": "蛇纹石化→滑石化→碳酸盐化",
                "key_elements": ["Ni", "Cu", "Co", "Pt", "Pd", "S", "Fe"],
                "element_association": "Ni-Cu-Co ± PGE",
                "geophysical_methods": ["磁法(基性岩体)", "重力(高密度)", "IP/电磁法(硫化物)", "MT"],
                "geophysical_anomalies": "硫化镍矿→高极化+低阻+重力高+磁异常",
            },
            {
                "name": "红土型镍矿(风化壳)",
                "tectonic_setting": "热带-亚热带超基性岩风化区",
                "host_rocks": "超基性岩风化壳(褐铁矿带+硅镁镍矿带)",
                "alteration": "红土化(热带风化)",
                "key_elements": ["Ni", "Co", "Fe", "Mn", "Mg", "Cr"],
                "element_association": "Ni-Co-Fe(红土剖面)",
                "geophysical_methods": ["磁法(红土盖层弱磁)", "放射性", "电法"],
                "geophysical_anomalies": "物探异常不显著(地表风化成因)",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 47, "unit": "ppm"},
        "georoc_rock_types": ["Peridotite", "Gabbro", "Norite", "Komatiite"],
    },

    "钴": {
        "metallogenic_types": [
            {
                "name": "沉积型钴矿(铜钴伴生)",
                "tectonic_setting": "刚果(金)加丹加铜矿带、赞比亚",
                "host_rocks": "含铜钴页岩/砂岩(氧化带富集)",
                "alteration": "氧化富集作用",
                "key_elements": ["Co", "Cu", "Ni", "Fe", "Mn", "Zn", "U"],
                "element_association": "Co-Cu ± U-Mn(刚果型)",
                "geophysical_methods": ["磁法", "IP(硫化物)", "放射性(伴生U)", "重力"],
                "geophysical_anomalies": "铜钴硫化物→高极化+低阻",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 17.3, "unit": "ppm"},
        "georoc_rock_types": ["Shale", "Sandstone", "Basalt", "Peridotite"],
    },

    "锑": {
        "metallogenic_types": [
            {
                "name": "热液型锑矿(锑汞伴生)",
                "tectonic_setting": "板内断裂带、碳酸盐岩台地(湘西、黔西南)",
                "host_rocks": "碳酸盐岩/碎屑岩(断裂控制)",
                "alteration": "硅化→碳酸盐化→黄铁矿化",
                "key_elements": ["Sb", "Hg", "Au", "As", "W", "Pb", "Zn", "F"],
                "element_association": "Sb-Hg-Au-As(低温热液组合)",
                "geophysical_methods": ["磁法(构造格架)", "电法(含锑石英脉→高阻)"],
                "geophysical_anomalies": "锑矿体→高阻(石英脉型)",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 0.4, "unit": "ppm"},
        "georoc_rock_types": ["Carbonate_rock", "Shale", "Granite"],
    },

    "铝土": {
        "metallogenic_types": [
            {
                "name": "红土型铝土矿",
                "tectonic_setting": "热带-亚热带稳定克拉通(几内亚、澳大利亚)",
                "host_rocks": "红土风化壳(铝硅酸盐岩风化产物)",
                "alteration": "红土化(铝富集、硅淋失)",
                "key_elements": ["Al", "Si", "Fe", "Ti", "Ga", "REE"],
                "element_association": "Al-Fe-Ti ± Ga-REE",
                "geophysical_methods": ["放射性(铝土矿→低K/Th/U)", "磁法(含铁红土)"],
                "geophysical_anomalies": "红土层→低放射性(铝富集层)",
            },
            {
                "name": "沉积型铝土矿",
                "tectonic_setting": "古隆起边缘碳酸盐岩侵蚀面(华北、贵州)",
                "host_rocks": "碳酸盐岩古侵蚀面之上",
                "alteration": "红土化→再沉积",
                "key_elements": ["Al", "Si", "Fe", "Ti", "Ga", "Li", "REE"],
                "element_association": "Al-Si-Fe ± Ga-Li",
                "geophysical_methods": ["重力(基底起伏)", "电法"],
                "geophysical_anomalies": "铝土矿层→低阻+低放射性",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 82000, "unit": "ppm"},
        "georoc_rock_types": ["Laterite", "Bauxite", "Carbonate_rock", "Basalt"],
    },

    "磷": {
        "metallogenic_types": [
            {
                "name": "海相沉积型磷矿",
                "tectonic_setting": "被动陆缘上升流区(扬子克拉通西缘、摩洛哥)",
                "host_rocks": "磷块岩(碳酸盐岩-硅质岩-磷块岩组合)",
                "alteration": "成岩磷酸盐化",
                "key_elements": ["P", "Ca", "F", "REE", "U"],
                "element_association": "P-Ca-F ± REE-U",
                "geophysical_methods": ["放射性(磷块岩→高γ)", "重力", "磁法"],
                "geophysical_anomalies": "磷块岩→高γ异常(U伴生)",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 757, "unit": "ppm"},
        "georoc_rock_types": ["Carbonate_rock", "Phosphorite", "Shale"],
    },

    "银": {
        "metallogenic_types": [
            {
                "name": "热液型银矿(铅锌银伴生)",
                "tectonic_setting": "陆缘弧、碰撞造山带",
                "host_rocks": "碳酸盐岩/碎屑岩(断裂+角砾岩带)",
                "alteration": "硅化→锰化→碳酸盐化→黄铁绢英岩化",
                "key_elements": ["Ag", "Pb", "Zn", "Cu", "Au", "Sb", "As", "Mn"],
                "element_association": "Ag-Pb-Zn ± Au-Sb(中低温热液)",
                "geophysical_methods": ["IP(硫化物)", "磁法(构造)", "重力", "电法"],
                "geophysical_anomalies": "银多金属硫化物→高极化+低阻",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 0.053, "unit": "ppm"},
        "georoc_rock_types": ["Granite", "Andesite", "Dacite", "Rhyolite"],
    },

    "钼": {
        "metallogenic_types": [
            {
                "name": "斑岩型钼矿",
                "tectonic_setting": "陆缘弧、板内花岗岩带(秦岭、华北克拉通南缘)",
                "host_rocks": "花岗斑岩/斑状花岗岩",
                "alteration": "钾化→绢英岩化→泥化→青磐岩化",
                "key_elements": ["Mo", "W", "Cu", "Au", "Re", "Ag", "Pb", "Zn"],
                "element_association": "Mo-W ± Cu-Re(斑岩核) → Pb-Zn-Ag(外围)",
                "geophysical_methods": ["磁法(岩体边界)", "IP(硫化物)", "重力(隐伏岩体)"],
                "geophysical_anomalies": "斑岩体→低磁+高极化(黄铁矿壳)→重力低",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 1.1, "unit": "ppm"},
        "georoc_rock_types": ["Granite", "Granodiorite", "Porphyry", "Quartz_monzonite"],
    },

    "锡": {
        "metallogenic_types": [
            {
                "name": "热液型锡矿(锡石-硫化物)",
                "tectonic_setting": "碰撞造山带S型花岗岩区(华南、东南亚)",
                "host_rocks": "花岗岩外接触带碳酸盐岩/碎屑岩",
                "alteration": "矽卡岩化→云英岩化→电气石化→绿泥石化",
                "key_elements": ["Sn", "W", "Cu", "Pb", "Zn", "Ag", "As", "Sb", "F", "B"],
                "element_association": "Sn-W-Cu(高温)→Sn-Pb-Zn-Ag(中温)",
                "geophysical_methods": ["磁法(矽卡岩带)", "重力(隐伏花岗岩)", "IP(硫化物)"],
                "geophysical_anomalies": "矽卡岩→磁异常+高密度；硫化物→高极化",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 1.7, "unit": "ppm"},
        "georoc_rock_types": ["Granite", "S-type_granite", "Greisen", "Pegmatite"],
    },

    "铂族": {
        "metallogenic_types": [
            {
                "name": "岩浆型铂族元素矿床",
                "tectonic_setting": "克拉通内基性-超基性层状侵入体(布什维尔德、大岩墙)",
                "host_rocks": "层状超基性-基性侵入体(辉石岩、铬铁岩、苏长岩)",
                "alteration": "岩浆结晶分异(塔尔纳赫型/麦伦斯基型)",
                "key_elements": ["Pt", "Pd", "Rh", "Ru", "Ir", "Os", "Ni", "Cu", "Cr"],
                "element_association": "Pt-Pd ± Ni-Cu-Cr",
                "geophysical_methods": ["磁法(基性岩层)", "重力(高密度层)", "IP(硫化物)", "电法"],
                "geophysical_anomalies": "硫化物层→高极化+重力高+磁异常",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": {"Pt": 0.0004, "Pd": 0.0004}, "unit": "ppm"},
        "georoc_rock_types": ["Peridotite", "Norite", "Gabbro", "Chromitite"],
    },

    "金刚石": {
        "metallogenic_types": [
            {
                "name": "金伯利岩型金刚石矿",
                "tectonic_setting": "古老克拉通核部(南非、西伯利亚、华北)",
                "host_rocks": "金伯利岩筒/岩脉",
                "alteration": "金伯利岩风化→黄土状覆盖",
                "key_elements": ["C", "Cr", "Ti", "Nb", "Ni", "Co", "Mg", "K"],
                "element_association": "指示矿物: 镁铝榴石+铬透辉石+钛铁矿+铬尖晶石",
                "geophysical_methods": ["磁法(金伯利岩筒→强磁异常)", "重力(低密度岩筒→重力低)", "电法", "放射性"],
                "geophysical_anomalies": "金伯利岩筒→圆形磁异常+重力低",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 200, "unit": "ppm (有机碳)"},
        "georoc_rock_types": ["Kimberlite", "Lamproite", "Peridotite"],
    },

    "钒钛": {
        "metallogenic_types": [
            {
                "name": "岩浆型钒钛磁铁矿",
                "tectonic_setting": "克拉通内基性岩体(攀枝花、布什维尔德)",
                "host_rocks": "辉长岩/斜长岩层状侵入体",
                "alteration": "岩浆结晶分异(晚期富集)",
                "key_elements": ["Fe", "Ti", "V", "Cr", "P", "S", "Co", "Ni"],
                "element_association": "Fe-Ti-V(钒钛磁铁矿层)",
                "geophysical_methods": ["磁法(强磁异常)", "重力(高密度层)"],
                "geophysical_anomalies": "钒钛磁铁矿→极强磁异常+重力高",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": {"V": 97, "Ti": 4100}, "unit": "ppm"},
        "georoc_rock_types": ["Gabbro", "Anorthosite", "Norite", "Titanomagnetite"],
    },

    "煤": {
        "metallogenic_types": [
            {
                "name": "陆相含煤盆地",
                "tectonic_setting": "克拉通内坳陷盆地(鄂尔多斯)、断陷盆地",
                "host_rocks": "含煤碎屑岩系(砂岩/泥岩/煤层)",
                "alteration": "煤化作用(泥炭→褐煤→烟煤→无烟煤)",
                "key_elements": ["C", "S", "灰分", "挥发分", "发热量"],
                "element_association": "高煤阶→高碳+低挥发分",
                "geophysical_methods": ["重力(盆地基底)", "电法(煤层→高阻)", "地震(煤层反射)", "测井(γ/电阻率)"],
                "geophysical_anomalies": "煤层→高电阻率+低γ(低灰分煤)",
            },
        ],
        "global_geochemical_background": {"note": "煤: 以工业指标(灰分/硫分/发热量)衡量", "unit": "—"},
        "georoc_rock_types": ["Sandstone", "Shale", "Mudstone"],
    },

    "铌钽": {
        "metallogenic_types": [
            {
                "name": "伟晶岩型铌钽矿",
                "tectonic_setting": "造山带、古老克拉通(刚果、巴西、澳大利亚)",
                "host_rocks": "花岗伟晶岩/LCT型伟晶岩",
                "alteration": "钠长石化→锂辉石化→铌钽矿化",
                "key_elements": ["Nb", "Ta", "Li", "Sn", "W", "Be", "Rb", "Cs"],
                "element_association": "Nb-Ta ± Li-Sn(伟晶岩型)",
                "geophysical_methods": ["放射性(含K/Th伟晶岩)", "磁法"],
                "geophysical_anomalies": "伟晶岩→低磁+局部放射性异常",
            },
            {
                "name": "风化壳型铌钽矿",
                "tectonic_setting": "热带-亚热带花岗岩/伟晶岩风化区",
                "host_rocks": "花岗岩风化壳",
                "alteration": "风化淋滤富集",
                "key_elements": ["Nb", "Ta", "REE", "Y", "Zr"],
                "element_association": "Nb-Ta-REE-Zr(风化富集)",
                "geophysical_methods": ["放射性(风化壳)", "磁法"],
                "geophysical_anomalies": "物探异常不显著",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": {"Nb": 12, "Ta": 0.7}, "unit": "ppm"},
        "georoc_rock_types": ["Pegmatite", "Granite", "Syenite", "Carbonatite"],
    },

    "铜钴": {
        "metallogenic_types": [
            {
                "name": "沉积型铜钴矿(刚果铜带型)",
                "tectonic_setting": "新元古代沉积盆地(刚果-赞比亚铜矿带)",
                "host_rocks": "含铜钴页岩/砂岩/白云岩",
                "alteration": "氧化富集(次生富集带)",
                "key_elements": ["Cu", "Co", "U", "Ni", "Zn", "Pb", "Ag", "Au", "Mn", "Fe"],
                "element_association": "Cu-Co ± U-Ni-Ag(氧化带)",
                "geophysical_methods": ["IP(硫化物)", "放射性(伴生U)", "磁法", "重力"],
                "geophysical_anomalies": "铜钴硫化物→高极化+低阻；伴生U→高γ",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": {"Cu": 27, "Co": 17.3}, "unit": "ppm"},
        "georoc_rock_types": ["Shale", "Sandstone", "Carbonate_rock"],
    },

    "铜镍": {
        "metallogenic_types": [
            {
                "name": "岩浆熔离铜镍硫化物矿床",
                "tectonic_setting": "克拉通边缘/造山带基性-超基性侵入体",
                "host_rocks": "基性-超基性岩(苏长岩/辉石岩/橄榄岩)",
                "alteration": "蛇纹石化→滑石化",
                "key_elements": ["Cu", "Ni", "Co", "Pt", "Pd", "S", "Fe"],
                "element_association": "Cu-Ni-Co ± PGE",
                "geophysical_methods": ["磁法(基性岩体)", "重力(高密度)", "IP/电磁法(硫化物)", "MT"],
                "geophysical_anomalies": "铜镍硫化物→高极化+低阻+重力高+磁异常",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": {"Cu": 27, "Ni": 47}, "unit": "ppm"},
        "georoc_rock_types": ["Gabbro", "Norite", "Peridotite", "Komatiite"],
    },

    "铜金": {
        "metallogenic_types": [
            {
                "name": "斑岩型铜金矿",
                "tectonic_setting": "岛弧/陆缘弧(环太平洋带、特提斯带)",
                "host_rocks": "石英闪长斑岩/花岗闪长斑岩",
                "alteration": "钾化→绢英岩化→泥化→青磐岩化",
                "key_elements": ["Cu", "Au", "Mo", "Ag", "Zn", "Pb", "As", "Sb"],
                "element_association": "Cu-Au ± Mo-Ag(核)→ Zn-Pb-As-Sb(外围)",
                "geophysical_methods": ["磁法(岩体)", "IP(硫化物)", "重力", "CSAMT"],
                "geophysical_anomalies": "斑岩体→低磁；硫化物壳→高极化",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": {"Cu": 27, "Au": 0.0015}, "unit": "ppm"},
        "georoc_rock_types": ["Granodiorite", "Diorite", "Quartz_diorite", "Andesite"],
    },

    "天然气": {
        "metallogenic_types": [
            {
                "name": "常规天然气成藏",
                "tectonic_setting": "前陆盆地、克拉通盆地、裂谷盆地",
                "host_rocks": "砂岩/碳酸盐岩储层",
                "alteration": "有机质热演化(Ro>1.3%生气窗)",
                "key_elements": ["TOC", "Ro", "孔隙度", "渗透率", "含气饱和度"],
                "element_association": "TOC>2%+Ro>1.3%+有效盖层",
                "geophysical_methods": ["三维地震", "重力", "磁法", "MT", "CSEM"],
                "geophysical_anomalies": "含气层→低频地震异常+高电阻率(测井)",
            },
            {
                "name": "页岩气",
                "tectonic_setting": "稳定克拉通内坳陷(四川盆地等)",
                "host_rocks": "富有机质页岩(龙马溪组/筇竹寺组等)",
                "alteration": "有机质成熟(Ro 1.0%-3.5%)",
                "key_elements": ["TOC", "Ro", "脆性指数", "含气量", "孔隙度", "页岩厚度"],
                "element_association": "TOC>2%+Ro>1.0%+脆性矿物>40%",
                "geophysical_methods": ["三维地震", "微地震(压裂)", "测井"],
                "geophysical_anomalies": "页岩→高电阻率(成熟段)+低泊松比",
            },
        ],
        "global_geochemical_background": {"note": "天然气: 以有机地球化学指标衡量", "unit": "—"},
        "georoc_rock_types": ["Shale", "Sandstone", "Carbonate_rock", "Mudstone"],
        "six_elements": [
            {"element": "烃源岩", "key_params": "TOC, Ro, 厚度", "description": "有效气源岩: TOC>1.5%, Ro>1.0%"},
            {"element": "储层", "key_params": "孔隙度, 渗透率", "description": "常规气: φ>8%; 页岩气: φ>3%"},
            {"element": "盖层", "key_params": "岩性, 厚度", "description": "膏盐岩>厚层泥岩"},
            {"element": "圈闭", "key_params": "类型, 面积", "description": "构造+岩性复合圈闭"},
            {"element": "运移", "key_params": "输导体系", "description": "断裂/不整合面/砂体"},
            {"element": "保存", "key_params": "构造活动, 盖层完整性", "description": "晚期构造破坏程度"},
        ],
    },

    "石墨": {
        "metallogenic_types": [
            {
                "name": "晶质石墨矿(片岩/片麻岩型)",
                "tectonic_setting": "古老克拉通变质岩区",
                "host_rocks": "石墨片岩/石墨片麻岩/大理岩",
                "alteration": "区域变质作用(有机质石墨化)",
                "key_elements": ["C", "S", "Si", "Al", "Fe"],
                "element_association": "C固定碳含量>3%(工业品位)",
                "geophysical_methods": ["电法(石墨→低阻)", "磁法", "放射性"],
                "geophysical_anomalies": "石墨矿→极低电阻率(良导体)",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 200, "unit": "ppm (有机碳)"},
        "georoc_rock_types": ["Schist", "Gneiss", "Marble", "Quartzite"],
    },

    "萤石": {
        "metallogenic_types": [
            {
                "name": "热液型萤石矿",
                "tectonic_setting": "断裂带、花岗岩外接触带(华北、华南)",
                "host_rocks": "碳酸盐岩/碎屑岩(断裂充填)",
                "alteration": "硅化→绢云母化→高岭土化",
                "key_elements": ["F", "Ca", "Ba", "Pb", "Zn", "Be", "Sb"],
                "element_association": "F-Ca ± Ba-Pb-Zn",
                "geophysical_methods": ["放射性(萤石→低U/Th)", "磁法(构造)"],
                "geophysical_anomalies": "萤石脉→低放射性",
            },
        ],
        "global_geochemical_background": {"crustal_abundance": 611, "unit": "ppm"},
        "georoc_rock_types": ["Granite", "Carbonate_rock", "Rhyolite"],
    },
}


def get_mineral_info(mineral: str) -> dict:
    """
    根据矿种名获取知识库信息

    Args:
        mineral: 矿种名（如 "铜"、"金"、"锂"）

    Returns:
        {
            "mineral": str,
            "metallogenic_types": [...],
            "all_key_elements": [...],  # 去重合并的所有指示元素
            "all_geophysical_methods": [...],  # 去重合并的所有推荐物探方法
            "recommended_data_priority": [...]  # 数据收集优先级
        }
    """
    mineral = _normalize(mineral)

    if mineral not in MINERAL_KNOWLEDGE:
        # 模糊匹配
        for key in MINERAL_KNOWLEDGE:
            if mineral in key or key in mineral:
                mineral = key
                break
        else:
            print(f"⚠️  矿种 '{mineral}' 不在知识库中，使用默认配置")  # noqa: T201
            return _default_info(mineral)

    kb = MINERAL_KNOWLEDGE[mineral]

    # 合并所有指示元素
    all_elements = set()
    for mt in kb["metallogenic_types"]:
        for elem in mt["key_elements"]:
            all_elements.add(elem)

    # 合并所有物探方法
    all_methods = set()
    for mt in kb["metallogenic_types"]:
        for method in mt["geophysical_methods"]:
            all_methods.add(method)

    return {
        "mineral": mineral,
        "metallogenic_types": kb["metallogenic_types"],
        "all_key_elements": sorted(all_elements),
        "all_geophysical_methods": sorted(all_methods),
        "global_background": kb["global_geochemical_background"],
        "georoc_rock_types": kb.get("georoc_rock_types", []),
        "recommended_data_priority": _compute_priority(all_methods),
        "six_elements": kb.get("six_elements", []),
        "exploration_indicators": kb.get("exploration_indicators", []),
    }


def _normalize(s: str) -> str:
    """标准化矿种名"""
    s = s.strip().replace(" ", "")
    # 先检查别名映射
    aliases = {
        "铜矿": "铜", "金矿": "金", "锂矿": "锂",
        "铅锌矿": "铅锌", "钨矿": "钨锡", "锡矿": "锡",
        "稀土矿": "稀土", "铁矿": "铁",
        "石油": "石油", "天然气": "天然气", "油气": "石油",
        "铀矿": "铀", "锰矿": "锰", "铬矿": "铬",
        "镍矿": "镍", "钴矿": "钴", "锑矿": "锑",
        "铝矿": "铝土", "铝土矿": "铝土", "磷矿": "磷",
        "银矿": "银", "钼矿": "钼", "铂矿": "铂族",
        "铂族金属": "铂族", "金刚石矿": "金刚石", "钻石": "金刚石",
        "钒钛磁铁": "钒钛", "钒钛矿": "钒钛",
        "煤矿": "煤", "煤炭": "煤",
        "铌矿": "铌钽", "钽矿": "铌钽", "铌钽矿": "铌钽",
        "铜钴矿": "铜钴", "铜镍矿": "铜镍", "铜金矿": "铜金",
        "石墨矿": "石墨", "萤石矿": "萤石",
    }
    if s in aliases:
        return aliases[s]
    # 再去掉尾缀"矿"字
    if s.endswith("矿"):
        s = s[:-1]
    return s


def _compute_priority(methods: set) -> list:
    """根据物探方法推荐数据获取优先级"""
    priority = []
    methods_str = ", ".join(methods)

    # 地震优先（石油）
    if any(m in methods_str for m in ["地震"]):
        priority.append({"rank": 1, "data": "全球 DEM 数据 (SRTM 30m)", "method": "盆地构造格架地形分析"})
        priority.append({"rank": 2, "data": "全球布格重力异常 (WGM2012)", "method": "盆地基底深度反演、构造单元划分"})
        priority.append({"rank": 3, "data": "全球航磁数据 (EMAG2 v3)", "method": "磁性基底埋深、断裂识别"})
        priority.append({"rank": 4, "data": "二维/三维地震数据", "method": "需与油田/矿权方合作获取 (核心数据，网上无公开)"})
        priority.append({"rank": 5, "data": "测井数据", "method": "需与油田/矿权方合作获取或购买"})
        priority.append({"rank": 6, "data": "区域地质图 + 盆地分析文献", "method": "NGAC + CNKI 检索"})
        return priority

    # 磁法几乎是所有固体矿种的基础
    if any(m in methods_str for m in ["磁法", "航磁"]):
        priority.append({"rank": 1, "data": "全球航磁数据 (EMAG2 v3)", "method": "自动下载 + 裁剪"})

    # 重力次之
    if any(m in methods_str for m in ["重力"]):
        priority.append({"rank": 2, "data": "全球布格重力异常 (WGM2012)", "method": "自动下载 + 裁剪"})

    # DEM 地形（自动下载出图，辅助构造/水系/蚀变地貌解译）
    priority.append({"rank": 3, "data": "DEM 地形数据 (SRTM 30m)", "method": "自动下载 + 出图"})

    # 电法
    if any(m in methods_str for m in ["IP", "激电", "CSAMT", "MT", "电磁法", "大地电磁"]):
        priority.append({"rank": 4, "data": "电法/电磁法数据", "method": "需野外施测 (无全国性公开数据)"})

    priority.append({"rank": 5, "data": "全国化探扫面数据 (图件)", "method": "NGAC 检索链接 + CNKI 文献检索"})

    return priority


def _default_info(mineral: str) -> dict:
    """默认知识库（未知矿种）"""
    return {
        "mineral": mineral,
        "metallogenic_types": [],
        "all_key_elements": [],
        "all_geophysical_methods": ["磁法", "重力", "IP/激电"],
        "global_background": {},
        "georoc_rock_types": [],
        "recommended_data_priority": [
            {"rank": 1, "data": "全球航磁数据 (EMAG2 v3)", "method": "自动下载 + 裁剪"},
            {"rank": 2, "data": "全球布格重力异常 (WGM2012)", "method": "自动下载 + 裁剪"},
            {"rank": 3, "data": "全国化探扫面数据 (图件)", "method": "NGAC 检索链接 + CNKI 文献检索"},
        ],
    }


def list_all_minerals() -> list:
    """列出知识库中所有支持的矿种"""
    return list(MINERAL_KNOWLEDGE.keys())


if __name__ == "__main__":
    import json
    for m in ["铜", "金", "锂", "铅锌", "钨锡", "稀土"]:
        info = get_mineral_info(m)
        print(f"\n{'='*60}")
        print(f"矿种: {info['mineral']}")
        print(f"成矿类型: {[t['name'] for t in info['metallogenic_types']]}")
        print(f"指示元素: {', '.join(info['all_key_elements'])}")
        print(f"推荐物探方法: {', '.join(info['all_geophysical_methods'])}")
