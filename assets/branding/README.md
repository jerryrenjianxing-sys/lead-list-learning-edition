# Media Deep Researcher Logo

2026-09-22 使用内置 ImageGen 生成三版，选用 `concept-a-m-lens.png`。M 对应 Media，负形放大镜对应研究；粗线条在窗口、托盘和小尺寸导航中易辨认。B 是多层研究镜，C 是折叠 M。三张 PNG 均保留原始输出，未描摹或手工重画。

网页 `webui/public/brand/media-deep-researcher.png` 为 128px；同目录 ICO 包含 16、20、24、32、40、48、64、128、256px。`packaging/build-brand-assets.ps1` 使用 Windows System.Drawing 做尺寸和格式转换，保留 alpha；不改变设计。C# 编译器嵌入 ICO，Velopack 的后续构建使用同一图标。

## 生成提示词

A、B 共用以下前缀，各自追加对应概念段：

```text
Use case: logo-brand. Create one finished, premium Windows app icon for "Media Deep Researcher", a social-media research workbench driven by the user's own AI agent. Single icon, square 1024x1024 composition. Flat, clean vector-like forms and deliberate negative space. A near-black midnight rounded-square tile filling about 92% of the canvas, with genuinely transparent area outside the rounded corners. One large geometric emblem centered within it, bold enough to be legible at 24 pixels. Palette: midnight #11151D, warm coral #EE6554, subtle pale ivory only if useful. Sophisticated research instrument, calm and editorial, not a cartoon. No wordmark, no text, no labels, no presentation board, no mockup, no watermarks, no tiny details, no glow, no 3D, no cast shadow.
```

A（已选）：

```text
Concept A: a distinctive capital M monogram constructed from two continuous, chunky geometric ribbons, with a small circular lens cut into the right shoulder in negative space. Evoke media signals being examined deeply. The M should read clearly before the lens. Restrained precise geometry, generous margins.
```

B：

```text
Concept B: an abstract research lens formed by three nested open arcs and a bold lower-right diagonal stem. The nested arcs suggest layers of evidence and depth, rather than a generic search icon. Add a single small coral focus point, integrated in the geometry. Elegant, highly reduced, strong silhouette.
```

C 的完整提示词（中断后重新生成）：

```text
Use case: logo-brand. Create one finished, premium Windows app icon for "Media Deep Researcher", a social-media research workbench driven by the user’s own AI agent. Single icon, square 1024x1024 composition. Flat, clean vector-like forms and deliberate negative space. A near-black midnight rounded-square tile filling about 92% of the canvas, with genuinely transparent area outside the rounded corners. One large geometric emblem centered within it, bold enough to be legible at 24 pixels. Palette: midnight #11151D and warm coral #EE6554. Concept C: a single folded coral ribbon that forms a compact angular M and a downward-pointing diamond in its central negative space. Symbolize diving beneath the surface to discover insight. Two or three large shapes maximum, balanced and unique. Sophisticated research instrument, calm and editorial. No wordmark, text, labels, watermark, board, mockup, glow, 3D, shadows or tiny details. Not a bird or envelope.
```
