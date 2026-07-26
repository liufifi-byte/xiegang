import fs from "node:fs/promises";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("报价单");
sheet.showGridLines = false;

const rows = [
  ["钢丝网骨架管110*1.6", "", 6, 22.00],
  ["钢丝网骨架管90*1.6", "", 6, 19.00],
  ["钢丝网骨架管75*1.6", "", 210, 17.00],
  ["钢丝网骨架管50*1.6", "", 180, 12.00],
  ["正三通75", "", 1, 13.35],
  ["90°弯头75", "", 10, 10.81],
  ["法兰头75", "", 6, 9.29],
  ["法兰盘片75", "", 6, 13.08],
  ["直通75", "", 35, 7.39],
  ["90°弯头90", "", 2, 15.89],
  ["法兰头90", "", 2, 11.27],
  ["法兰盘片90", "", 2, 14.17],
  ["90°弯头110", "", 2, 19.89],
  ["直通110", "", 5, 11.84],
  ["法兰头110", "", 5, 14.86],
  ["法兰盘片110", "", 5, 15.26],
  ["正三通50", "", 10, 8.64],
  ["90°弯头50", "", 55, 6.38],
  ["直通50", "", 30, 5.17],
  ["法兰头50", "", 55, 6.10],
  ["法兰盘片50", "", 55, 9.81],
  ["皮垫110", "", 66, 1.50],
  ["皮垫90", "", 5, 1.00],
  ["皮垫75", "", 15, 0.70],
  ["皮垫50", "", 80, 0.50],
  ["螺丝螺母16*10（160-200）", "", 600, 2.20],
  ["螺丝螺母14*8（75-110）", "", 400, 1.80],
];

sheet.mergeCells("A1:F2");
sheet.getRange("A1").values = [["安徽博大鑫丰塑业有限公司报价单"]];
sheet.mergeCells("A3:D3");
sheet.getRange("A3").values = [["单位名称：安徽博大鑫丰塑业有限公司"]];
sheet.mergeCells("E3:F3");
sheet.getRange("E3").values = [["日期："]];
sheet.mergeCells("A4:D4");
sheet.getRange("A4").values = [["开户行：中国建设银行股份有限公司滁州政务新区支行"]];
sheet.mergeCells("E4:F4");
sheet.getRange("E4").values = [["联系人："]];
sheet.mergeCells("A5:D5");
sheet.getRange("A5").values = [["银行账号：3405 0173 8908 0000 0352"]];
sheet.mergeCells("E5:F5");
sheet.getRange("E5").values = [["地址："]];

sheet.getRange("A6:F6").values = [["序号", "产品及规格型号", "单位", "数量", "单价", "金额"]];
sheet.getRange("A7:D33").values = rows.map((r, i) => [i + 1, ...r.slice(0, 3)]);
sheet.getRange("E7:E33").values = rows.map(r => [r[3]]);
sheet.getRange("F7").formulas = [["=D7*E7"]];
sheet.getRange("F7:F33").fillDown();
sheet.mergeCells("A34:D34");
sheet.getRange("A34").values = [[""]];
sheet.getRange("E34").values = [["合计"]];
sheet.getRange("F34").formulas = [["=SUM(F7:F33)"]];

sheet.getRange("A1:F34").format.font = { name: "宋体", size: 11, color: "#000000" };
sheet.getRange("A1").format.font = { name: "宋体", size: 22, bold: false, color: "#000000" };
sheet.getRange("A1:F2").format.horizontalAlignment = "center";
sheet.getRange("A1:F2").format.verticalAlignment = "center";
sheet.getRange("A3:F5").format.font = { name: "宋体", size: 11, bold: true, color: "#000000" };
sheet.getRange("A3:F5").format.verticalAlignment = "center";
sheet.getRange("A6:F6").format.font = { name: "宋体", size: 11, bold: true, color: "#000000" };
sheet.getRange("A6:F34").format.horizontalAlignment = "center";
sheet.getRange("B7:B33").format.horizontalAlignment = "center";
sheet.getRange("E7:F34").format.numberFormat = "0.00";
sheet.getRange("D7:D33").format.numberFormat = "0";
sheet.getRange("A3:F34").format.borders = { preset: "all", style: "thin", color: "#000000" };
sheet.getRange("A1:F2").format.borders = {
  top: { style: "thin", color: "#000000" },
  bottom: { style: "thin", color: "#000000" },
};

sheet.getRange("A1:F2").format.rowHeight = 26;
sheet.getRange("A3:F5").format.rowHeight = 20;
sheet.getRange("A6:F34").format.rowHeight = 20;
sheet.getRange("A:A").format.columnWidth = 8;
sheet.getRange("B:B").format.columnWidth = 31;
sheet.getRange("C:C").format.columnWidth = 12;
sheet.getRange("D:D").format.columnWidth = 12;
sheet.getRange("E:E").format.columnWidth = 16;
sheet.getRange("F:F").format.columnWidth = 16;
sheet.getRange("G1:G34").format.fill = "#B7F7B7";
sheet.getRange("G:G").format.columnWidth = 10;

sheet.freezePanes.freezeRows(6);
sheet.getRange("A1:G34").format.wrapText = false;

const inspection = await workbook.inspect({
  kind: "table",
  range: "报价单!A1:F34",
  include: "values,formulas",
  tableMaxRows: 40,
  tableMaxCols: 6,
});
console.log(inspection.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 50 },
  summary: "formula error scan",
});
console.log(errors.ndjson);

const outputDir = "/Users/xinyiliu/Desktop/xiegang/outputs/019f9d67-ca9a-7b11-a185-1d13ae70d7b2";
await fs.mkdir(outputDir, { recursive: true });
const preview = await workbook.render({ sheetName: "报价单", range: "A1:G34", scale: 1.5, format: "png" });
await fs.writeFile(`${outputDir}/preview.png`, new Uint8Array(await preview.arrayBuffer()));
const file = await SpreadsheetFile.exportXlsx(workbook);
await file.save(`${outputDir}/安徽博大鑫丰塑业有限公司报价单.xlsx`);
await file.save("/Users/xinyiliu/Desktop/安徽博大鑫丰塑业有限公司报价单.xlsx");
