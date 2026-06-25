import { Typography, Collapse, Divider } from 'antd'

const { Text, Title, Paragraph } = Typography

export default function HelpPanel() {
  return (
    <div style={{ fontSize: 13, lineHeight: 1.8 }}>
      <Title level={5} style={{ marginTop: 0 }}>系统使用说明</Title>

      <Collapse
        defaultActiveKey={['1']}
        ghost
        size="small"
        items={[
          {
            key: '1',
            label: '一、系统简介',
            children: (
              <div>
                <Paragraph style={{ fontSize: 13 }}>
                  本系统用于<strong>深部矿产资源预测</strong>，基于尖点突变理论分析七个地质慢变量，
                  自动圈定有利成矿靶区。
                </Paragraph>
                <Paragraph style={{ fontSize: 13 }}>
                  <Text strong>核心公式：</Text>
                  <Text code>Δ = b² + (8/27) × a³</Text>
                </Paragraph>
                <ul style={{ paddingLeft: 18, margin: '4px 0' }}>
                  <li><Text strong>b（驱动力）</Text>：由①地应力、②氧逸度、③流体超压、④断裂活动性、⑤化学势五个变量加权合成</li>
                  <li><Text strong>a（阻力）</Text>：由⑥盖层封闭性和⑦温度梯度两个变量加权合成</li>
                  <li><Text strong>Δ &lt; 0</Text>的区域判定为有利成矿区（靶区）</li>
                </ul>
              </div>
            ),
          },
          {
            key: '2',
            label: '二、数据准备',
            children: (
              <div>
                <Paragraph style={{ fontSize: 13 }}>
                  系统需要两类输入数据：<Text strong>研究区边界</Text>和<Text strong>卫星遥感数据</Text>。
                </Paragraph>

                <Text strong style={{ display: 'block', marginTop: 8 }}>1. 研究区边界</Text>
                <Paragraph style={{ fontSize: 13 }}>
                  支持两种格式：
                </Paragraph>
                <ul style={{ paddingLeft: 18, margin: '4px 0' }}>
                  <li><Text code>.kml</Text> / <Text code>.ovkml</Text> 文件 — Google Earth、QGIS 或奥维地图导出的矢量边界</li>
                  <li><Text code>.xlsx</Text> / <Text code>.xls</Text> 文件 — Excel 坐标表</li>
                </ul>
                <Paragraph style={{ fontSize: 13, color: '#666' }}>
                  Excel 格式要求：每行一个坐标点，包含经度和纬度两列（可附加序号列，系统自动识别），
                  按顺序连成多边形边界。首行可为表头（自动跳过）。至少需要 3 个坐标点。
                </Paragraph>

                <Divider style={{ margin: '8px 0' }} />

                <Text strong style={{ display: 'block' }}>2. 卫星遥感数据</Text>
                <Paragraph style={{ fontSize: 13 }}>
                  打包成一个 <Text code>.zip</Text> 压缩包上传，系统自动识别文件。
                  文件命名建议包含卫星名称和波段号，如 <Text code>sentinel2_b03.tif</Text>、<Text code>aster_b05.tif</Text>。
                </Paragraph>

                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse', marginTop: 4 }}>
                  <thead>
                    <tr style={{ background: '#fafafa' }}>
                      <th style={{ padding: '4px 8px', textAlign: 'left', borderBottom: '1px solid #eee' }}>卫星/数据</th>
                      <th style={{ padding: '4px 8px', textAlign: 'left', borderBottom: '1px solid #eee' }}>波段</th>
                      <th style={{ padding: '4px 8px', textAlign: 'left', borderBottom: '1px solid #eee' }}>是否必填</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr><td style={{ padding: '2px 8px' }}>DEM</td><td style={{ padding: '2px 8px' }}>高程数据 (SRTM/ASTER GDEM)</td><td style={{ padding: '2px 8px', color: 'red' }}>必填</td></tr>
                    <tr><td style={{ padding: '2px 8px' }}>Sentinel-2</td><td style={{ padding: '2px 8px' }}>B03(绿), B04(红), B08(近红外)</td><td style={{ padding: '2px 8px', color: 'red' }}>必填</td></tr>
                    <tr><td style={{ padding: '2px 8px' }}>ASTER SWIR</td><td style={{ padding: '2px 8px' }}>B05, B06, B07, B08</td><td style={{ padding: '2px 8px', color: 'red' }}>必填</td></tr>
                    <tr><td style={{ padding: '2px 8px' }}>ASTER TIR</td><td style={{ padding: '2px 8px' }}>B10, B11, B12, B13, B14</td><td style={{ padding: '2px 8px', color: 'red' }}>必填</td></tr>
                    <tr><td style={{ padding: '2px 8px' }}>InSAR</td><td style={{ padding: '2px 8px' }}>速度场/形变</td><td style={{ padding: '2px 8px' }}>可选</td></tr>
                    <tr><td style={{ padding: '2px 8px' }}>InSAR</td><td style={{ padding: '2px 8px' }}>相干性（与速度场配套使用）</td><td style={{ padding: '2px 8px' }}>可选</td></tr>
                  </tbody>
                </table>
              </div>
            ),
          },
          {
            key: '3',
            label: '三、操作步骤',
            children: (
              <div>
                <Text strong style={{ display: 'block' }}>步骤 1：上传研究区边界</Text>
                <Paragraph style={{ fontSize: 13 }}>
                  在"研究区边界"区域点击或拖入 KML / Excel 文件。上传成功后显示绿色勾号。
                </Paragraph>

                <Text strong style={{ display: 'block', marginTop: 8 }}>步骤 2：上传卫星数据</Text>
                <Paragraph style={{ fontSize: 13 }}>
                  将所有卫星数据 GeoTIFF 文件打成一个 ZIP 包，拖入"卫星遥感数据"区域。
                  系统自动识别并匹配文件到对应的数据槽位。匹配结果会显示哪些文件已识别、哪些缺失。
                </Paragraph>
                <Paragraph style={{ fontSize: 13, color: '#666' }}>
                  如有缺失的必填文件，页面会显示补充上传区域，可以单独拖入对应文件。
                </Paragraph>

                <Text strong style={{ display: 'block', marginTop: 8 }}>步骤 3：开始分析</Text>
                <Paragraph style={{ fontSize: 13 }}>
                  所有必填数据就绪后，点击"开始分析"按钮。系统执行 14 步自动分析流水线，
                  底部状态栏实时显示进度和当前步骤。整个分析通常需要几十秒到几分钟。
                </Paragraph>

                <Text strong style={{ display: 'block', marginTop: 8 }}>步骤 4：查看结果</Text>
                <Paragraph style={{ fontSize: 13 }}>
                  分析完成后自动切换到结果视图：
                </Paragraph>
                <ul style={{ paddingLeft: 18, margin: '4px 0' }}>
                  <li><Text strong>地图视图</Text>：右侧地图显示各图层叠加，左侧可控制各图层的开关、透明度</li>
                  <li><Text strong>📊 分析结果</Text>：查看各慢变量的统计摘要（最小值、最大值、均值、标准差）</li>
                  <li><Text strong>下载 GeoTIFF</Text>：每个图层可单独下载，用于 QGIS / ArcGIS 等专业软件查看</li>
                </ul>
              </div>
            ),
          },
          {
            key: '4',
            label: '四、参数调节（可选）',
            children: (
              <div>
                <Paragraph style={{ fontSize: 13 }}>
                  分析完成后，可在"⚙️ 参数设置"页面调整参数后重新分析（无需重新上传数据）：
                </Paragraph>
                <ul style={{ paddingLeft: 18, margin: '4px 0' }}>
                  <li><Text strong>驱动力权重 (b)</Text>：六个驱动力的权重，滑块调节后自动归一化（总和=100%）</li>
                  <li><Text strong>阻力权重 (a)</Text>：盖层和温度阻力的权重分配</li>
                  <li><Text strong>Δ阈值</Text>：靶区判定阈值，越负越严格（默认 -5000）</li>
                  <li><Text strong>高斯平滑 Sigma</Text>：控制结果的空间平滑程度（默认 3）</li>
                </ul>
                <Paragraph style={{ fontSize: 13 }}>
                  调节参数后点击"重新分析"，系统从已缓存的数据重新计算，速度比首次分析快。
                </Paragraph>
              </div>
            ),
          },
          {
            key: '5',
            label: '五、七个慢变量说明',
            children: (
              <div style={{ fontSize: 12 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ background: '#fafafa' }}>
                      <th style={{ padding: '4px 6px', textAlign: 'left', borderBottom: '1px solid #eee' }}>#</th>
                      <th style={{ padding: '4px 6px', textAlign: 'left', borderBottom: '1px solid #eee' }}>慢变量</th>
                      <th style={{ padding: '4px 6px', textAlign: 'left', borderBottom: '1px solid #eee' }}>输入数据</th>
                      <th style={{ padding: '4px 6px', textAlign: 'left', borderBottom: '1px solid #eee' }}>角色</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr><td style={{ padding: '3px 6px' }}>①</td><td style={{ padding: '3px 6px' }}>地应力异常梯度 τ</td><td style={{ padding: '3px 6px' }}>DEM / InSAR</td><td style={{ padding: '3px 6px' }}>驱动力</td></tr>
                    <tr><td style={{ padding: '3px 6px' }}>②</td><td style={{ padding: '3px 6px' }}>氧逸度突变带 Δlog fO₂</td><td style={{ padding: '3px 6px' }}>ASTER SWIR 铁氧化物指数</td><td style={{ padding: '3px 6px' }}>驱动力</td></tr>
                    <tr><td style={{ padding: '3px 6px' }}>③</td><td style={{ padding: '3px 6px' }}>流体超压指数 λ</td><td style={{ padding: '3px 6px' }}>ASTER TIR + S2 NDVI</td><td style={{ padding: '3px 6px' }}>驱动力</td></tr>
                    <tr><td style={{ padding: '3px 6px' }}>④</td><td style={{ padding: '3px 6px' }}>断裂活动性 A</td><td style={{ padding: '3px 6px' }}>应力梯度 → Canny检测</td><td style={{ padding: '3px 6px' }}>驱动力</td></tr>
                    <tr><td style={{ padding: '3px 6px' }}>⑤</td><td style={{ padding: '3px 6px' }}>化学势梯度 ∇μ</td><td style={{ padding: '3px 6px' }}>S2 B3,B4 + ASTER B5,B7</td><td style={{ padding: '3px 6px' }}>驱动力</td></tr>
                    <tr><td style={{ padding: '3px 6px' }}>⑥</td><td style={{ padding: '3px 6px' }}>盖层封闭性 ΔP</td><td style={{ padding: '3px 6px' }}>ASTER B6, B7, B8 碳酸盐指数</td><td style={{ padding: '3px 6px' }}>阻力</td></tr>
                    <tr><td style={{ padding: '3px 6px' }}>⑦</td><td style={{ padding: '3px 6px' }}>温度异常梯度 ∇T</td><td style={{ padding: '3px 6px' }}>ASTER TIR 傅里叶热传导</td><td style={{ padding: '3px 6px' }}>阻力</td></tr>
                  </tbody>
                </table>
              </div>
            ),
          },
          {
            key: '6',
            label: '六、常见问题',
            children: (
              <div>
                <Text strong>Q：下载的 GeoTIFF 文件打不开？</Text>
                <Paragraph style={{ fontSize: 13, color: '#666' }}>
                  GeoTIFF 是专业地理栅格格式，macOS "预览"不支持。请使用 QGIS（免费）或 ArcGIS 打开。
                  下载链接：<Text code>https://qgis.org</Text>
                </Paragraph>

                <Text strong style={{ display: 'block', marginTop: 8 }}>Q：ZIP 包中的文件没有被识别？</Text>
                <Paragraph style={{ fontSize: 13, color: '#666' }}>
                  文件名需包含波段号（如 b05、B10）和卫星标识（sentinel/aster）。
                  如果自动识别失败，可以在匹配结果页面单独补充上传。
                </Paragraph>

                <Text strong style={{ display: 'block', marginTop: 8 }}>Q：分析结果全部显示"无有效数据"？</Text>
                <Paragraph style={{ fontSize: 13, color: '#666' }}>
                  可能原因：① 卫星数据范围与研究区边界不重叠 ② 数据文件缺少坐标参考系统（CRS）信息
                  ③ 数据分辨率与边界范围不匹配。请检查输入数据的覆盖范围。
                </Paragraph>

                <Text strong style={{ display: 'block', marginTop: 8 }}>Q：Excel 坐标文件上传后坐标不对？</Text>
                <Paragraph style={{ fontSize: 13, color: '#666' }}>
                  Excel 中经度范围 -180~180，纬度范围 -90~90。系统自动识别哪列是经度、哪列是纬度。
                  如果有多余的序号列，系统会自动跳过。
                </Paragraph>

                <Text strong style={{ display: 'block', marginTop: 8 }}>Q：其他设备如何访问？</Text>
                <Paragraph style={{ fontSize: 13, color: '#666' }}>
                  同一局域网内的设备可访问 <Text code>http://服务器IP:5173</Text>。
                </Paragraph>
              </div>
            ),
          },
        ]}
      />
    </div>
  )
}
