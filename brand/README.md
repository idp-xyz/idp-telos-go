# IDP TELOS · 最终 Logo 品牌资产包 v1.0

**设计基线已锁定，不再主动改变字形。**

- 正式产品品牌与拟使用/申请的商标文字：**IDP TELOS**。
- 视觉主字标：**TELOS**（仅五个字母；三横 E）。
- 唯一设计源母稿：`07_Source_Reference/TELOS_Original_Approved_Reference.jpeg`，1536×512。
- 原图可见字形位置：x 约 535–1014、y 约 197–294；可见宽高比约 **4.90:1**。
- 字形路径来自此前确认的原版锁定 SVG，未重新设计五个字母，不压窄或拉宽 O/E/T 等。SVG 追踪在亚像素处可能与 JPEG 略有区别。
- Binary E 作为原 E 位置和宽度范围内的派生视觉；只有 0/1 变化，字外没有数字背景；T/L/O/S 原始路径不变。

## 文件夹

| 路径 | 内容 |
| --- | --- |
| `01_Master/` | 原版三横 E 的正式静态矢量母版：深色、透明、浅色、单色、紧凑透明画板 |
| `02_BinaryE/` | Binary E 静态与无缝连续动态的暗底/透明 SVG（含 UI 紧凑透明画板） |
| `03_PNG/` | 3840×1280、1536×512、透明 3000px、UI 留白裁剪版 PNG |
| `04_Icons/` | 从母版三横 E 提取的**次级功能图标**、多尺寸 PNG、favicon.ico、apple-touch-icon |
| `05_Brand_Guidelines/` | 离线互动品牌规范 HTML（另附嵌入图片的单文件版本） |
| `06_Engineering/` | Token JSON、CSS 及 React 组件示例、集成说明 |
| `07_Source_Reference/` | 1536×512 原始用户确认 JPEG（位图，不冒充可编辑矢量） |
| `08_Previews/` | 原图与矢量对比、高清展示图 |
| `manifest.sha256.json` | 每个文件 SHA-256 校验值 |

## 使用原则

1. 静态主标识默认采用 `01_Master/TELOS_Primary_Locked_Dark.svg`，在透明环境使用 `01_Master/TELOS_Primary_Locked_Cropped_Transparent.svg`。
2. 动态营销场景使用 `02_BinaryE/TELOS_BinaryE_Animated_Dark.svg`；在低动态偏好时切换到静态 SVG。
3. 禁止拉伸或重绘字形、禁止增加数字背景和装饰。
4. **图标是功能适配**，不能代替正式主字标的品牌/商标展示。
5. 目前不提供注册商标证明；Logo 文字及图形对外使用前仍需商标、近似与设计权利核查。
6. 本包无第三方字体文件。所有字标 SVG 采用路径，不依赖字体安装。

## 文件状态与证据

- 原版三个源 SVG 已原样复制为包内对应母稿/派生文件；附文件哈希。
- 颜色、画布与透明背景等派生变化均**未改变字形路径 d**。
- Binary E 里的 T/L/O/S 路径与静态母版字节级一致。
- 规范中的 4.90:1 是**字形可见边界比例**，不是每个导出文件的画布比例。
