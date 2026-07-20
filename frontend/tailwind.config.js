/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx}",
    "./components/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // 压舱色（顶栏）：午夜墨蓝——奇葩说舞台的克制版
        parliament: {
          DEFAULT: "#0E122A",   // 午夜墨蓝
          deep:     "#070A1C",   // 更深一层
        },
        gold: {
          DEFAULT: "#FFD93D",   // 奇葩说金（高光、点缀）
          muted:    "#E8B441",   // 暖金（备用）
        },

        // 背景层
        "bg-home":    "#FFFFFF",   // 首页白底（让标题呼吸）
        "bg-chat":    "#FFFFFF",   // 二级聊天页
        "bg-surface": "#F4EFE6",   // 卡片：米色（让卡片浮起来）
        "bg-elev":    "#EFE8D8",   // 悬浮/输入：更深一点的米色

        // 文字层
        ink: {
          DEFAULT: "#1A1F2E",   // 深墨蓝（替代纯黑，更克制）
          2:      "#4A5263",    // 议席灰
          3:      "#8089A0",    // 静观灰
          4:      "#B8BFC8",    // 极弱
        },

        // 分割/边框
        edge: {
          DEFAULT: "#E8E5DA",   // 米边
          strong:  "#1A1F2E",   // 议员卡片深边框（舞台重量）
        },

        // 强调双色（奇葩说系）
        brand: {
          DEFAULT: "#1A9CA7",   // 飞鸟蓝（替代之前的绿）
          hover:   "#167A82",
          bg:      "#E1F4F5",
          text:    "#0F6A72",
        },

        // 5 议员色 → 奇葩说化（保留辨色度）
        "m-suan":  "#2B4F8C",   // 老算 深青蓝
        "m-chen":  "#DC3F4B",   // 老沉 番茄红（奇葩说正方色）
        "m-ji":    "#E8B441",   // 小机 暖金（奇葩说金句色）
        "m-nian":  "#1E7C5B",   // 十年 竺绿
        "m-fan":   "#5C3D6E",   // 反对 葡萄紫（保留）

        // 立场标签（奇葩说正反方色彩）
        "stance": {
          forBg:    "#FFE6E6",
          forText:  "#DC3F4B",    // 红 = 支持（奇葩说正方）
          againstBg:"#E1F4F5",
          againstText:"#0F6A72",  // 蓝 = 反对（奇葩说反方）
          neutralBg:"#F1EFEC",
          neutralText:"#4A5263",
        },

        // "我"的气泡色：暖橙（奇葩说金句光环色）
        "me": {
          DEFAULT: "#FF8551",   // 金句橙
          hover:   "#FF6A33",
        },
      },
      fontFamily: {
        sans: ['"PingFang SC"', '"Microsoft YaHei"', '"Segoe UI"',
               '-apple-system', 'BlinkMacSystemFont', 'Helvetica', 'sans-serif'],
        serif: ['Georgia', '"Source Serif Pro"', '"Songti SC"',
                '"PingFang SC"', 'serif'],
      },
    },
  },
  plugins: [],
};
