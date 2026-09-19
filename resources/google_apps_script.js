/**
 * Daily Restaurant Lead Scout - Google Sheet Webhook Script
 * 
 * 功能特性：
 * 1. doGet: 当用户在浏览器直接点击 Webhook 链接时，自动跳转到对应的 Google Sheet 在线表格。
 * 2. doPost: 每次执行自动创建以拜访日期和方向命名的独立子表 (Tab，例如 '2026-09-18 (EAST)')，并自动置顶到第 1 个标签页。
 * 3. 样式美化: 自动应用 Google Material 视觉排版，包括首行冻结、品牌蓝色表头、交替斑马线背景、细边框及自适应列宽。
 *
 * 部署步骤：
 * 1. 打开您的目标 Google Sheet，点击顶部菜单【扩展程序】->【Apps 脚本】。
 * 2. 将本文件的完整代码复制并覆盖粘贴到编辑器中，点击【保存】(Ctrl+S / Cmd+S)。
 * 3. 点击右上角【部署】->【新建部署】：
 *    - 选择类型：Web 应用 (Web app)
 *    - 说明 (Description)：Daily Lead Scout Sync
 *    - 执行身份 (Execute as)：我 (Me)
 *    - 谁可以访问 (Who has access)：所有人 (Anyone)
 * 4. 点击【部署】，在授权提示中选择您的 Google 账号并允许访问。
 * 5. 复制生成的 Web 应用网址 (URL，格式如 https://script.google.com/macros/s/.../exec)。
 * 6. 将该 URL 提供给智能体或配置到项目中即可。
 */

/**
 * HTTP GET: 浏览器直接访问时自动重定向至当前 Google Sheet 在线表格
 */
function doGet(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheetUrl = ss.getUrl();
  var html = '<!DOCTYPE html><html><head>' +
    '<meta http-equiv="refresh" content="0; url=' + sheetUrl + '" />' +
    '<title>正在打开 Google Sheet...</title>' +
    '<style>' +
    'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;height:80vh;background:#f8fafc;color:#1e293b;margin:0;}' +
    '.card{background:#ffffff;padding:32px 48px;border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,0.06);text-align:center;max-width:480px;}' +
    'h2{margin-top:0;font-size:20px;color:#0f172a;}' +
    'p{color:#64748b;font-size:14px;line-height:1.6;margin-bottom:24px;}' +
    '.btn{display:inline-block;padding:12px 24px;background:#1a73e8;color:#ffffff;text-decoration:none;border-radius:6px;font-weight:600;font-size:14px;}' +
    '</style>' +
    '</head><body>' +
    '<div class="card">' +
    '<h2>正在跳转至 Google Sheet...</h2>' +
    '<p>如果浏览器没有自动跳转，请点击下方按钮直接打开在线表格：</p>' +
    '<a class="btn" href="' + sheetUrl + '" target="_blank">打开 Google Sheet 在线表格</a>' +
    '</div>' +
    '</body></html>';
  return HtmlService.createHtmlOutput(html)
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/**
 * HTTP POST: 接收每日拓客路线数据并落盘至独立子表
 */
function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var ss = SpreadsheetApp.getActiveSpreadsheet();

    // 1. 获取或创建独立子表 Tab（如 '2026-09-18 (EAST)'）
    // 同一天多次执行时不覆盖清空旧数据，自动创建带序号的新子表（如 '2026-09-18 (EAST) (2)'）
    var baseTitle = data.sheet_name || ("Route_" + Utilities.formatDate(new Date(), "GMT-4", "yyyy-MM-dd"));
    var sheetName = baseTitle;
    var counter = 2;
    while (ss.getSheetByName(sheetName)) {
      sheetName = baseTitle + " (" + counter + ")";
      counter++;
    }
    var sheet = ss.insertSheet(sheetName);

    // 将最新子表置顶到最左侧第一个标签
    ss.setActiveSheet(sheet);
    ss.moveActiveSheet(1);

    // 2. 写入大地图导航条（超链接）
    var startRow = 1;
    var headers = data.headers || ["No.", "Restaurant Name", "Address", "Navigation Address", "Opening Hours", "Phone", "Fried Food Evidence"];
    if (data.master_nav_url) {
      sheet.getRange(1, 1).setFormula('=HYPERLINK("' + data.master_nav_url + '", "Google Maps Route Navigation")');
      sheet.getRange(1, 1, 1, headers.length).merge()
        .setBackground("#E8F0FE")
        .setFontColor("#1A73E8")
        .setFontWeight("bold")
        .setFontSize(10)
        .setHorizontalAlignment("center")
        .setVerticalAlignment("middle");
      sheet.setRowHeight(1, 34);
      startRow = 2;
    }

    // 3. 写入规范表头
    var headerRange = sheet.getRange(startRow, 1, 1, headers.length);
    headerRange.setValues([headers]);
    headerRange.setBackground("#1A73E8")
      .setFontColor("#FFFFFF")
      .setFontWeight("bold")
      .setFontSize(11)
      .setHorizontalAlignment("center")
      .setVerticalAlignment("middle");
    sheet.setRowHeight(startRow, 38);
    sheet.setFrozenRows(startRow); // 冻结表头

    // 4. 写入并格式化数据行
    if (data.rows && data.rows.length > 0) {
      var dataStartRow = startRow + 1;
      var numRows = data.rows.length;
      var dataRange = sheet.getRange(dataStartRow, 1, numRows, headers.length);
      dataRange.setValues(data.rows);
      dataRange.setFontSize(10).setVerticalAlignment("middle");

      // 斑马线交替底色与行高
      for (var r = 0; r < numRows; r++) {
        var rowNum = dataStartRow + r;
        sheet.setRowHeight(rowNum, 28);
        var rowBg = (r % 2 === 0) ? "#FFFFFF" : "#F8FAFC";
        sheet.getRange(rowNum, 1, 1, headers.length).setBackground(rowBg);
      }

      // 细边框设置
      dataRange.setBorder(true, true, true, true, true, true, "#E2E8F0", SpreadsheetApp.BorderStyle.SOLID);

      // 列对齐与换行策略
      sheet.getRange(dataStartRow, 1, numRows, 1).setHorizontalAlignment("center"); // 序号居中
      sheet.getRange(dataStartRow, 2, numRows, 1).setFontWeight("bold");             // 店名加粗
      sheet.getRange(dataStartRow, 3, numRows, 1).setWrapStrategy(SpreadsheetApp.WrapStrategy.WRAP); // 详细地址自动换行
      sheet.getRange(dataStartRow, 4, numRows, 1).setWrapStrategy(SpreadsheetApp.WrapStrategy.WRAP); // 导航地址自动换行
      sheet.getRange(dataStartRow, 5, numRows, 1).setHorizontalAlignment("center"); // 营业时间居中
      sheet.getRange(dataStartRow, 6, numRows, 1).setHorizontalAlignment("center"); // 电话居中
      sheet.getRange(dataStartRow, 7, numRows, 1).setWrapStrategy(SpreadsheetApp.WrapStrategy.WRAP); // 油炸依据自动换行

      // 5. 合并第 4 列（Navigation Address）中连续相同的导航地址单元格
      var navCol = 4;
      var mergeStart = 0;
      for (var r = 0; r < numRows; r++) {
        var curNav = data.rows[r][navCol - 1];
        var nextNav = (r + 1 < numRows) ? data.rows[r + 1][navCol - 1] : null;
        if (curNav && curNav === nextNav) {
          // 连续相同，继续累积
        } else {
          if (r > mergeStart) {
            var startRowIdx = dataStartRow + mergeStart;
            var mergeHeight = r - mergeStart + 1;
            sheet.getRange(startRowIdx, navCol, mergeHeight, 1).merge()
              .setVerticalAlignment("middle")
              .setHorizontalAlignment("left")
              .setWrapStrategy(SpreadsheetApp.WrapStrategy.WRAP);
          }
          mergeStart = r + 1;
        }
      }
    }

    // 6. 设置自适应列宽
    sheet.setColumnWidth(1, 60);   // 序号
    sheet.setColumnWidth(2, 220);  // 餐馆名称
    sheet.setColumnWidth(3, 300);  // 详细地址
    sheet.setColumnWidth(4, 300);  // 导航地址
    sheet.setColumnWidth(5, 180);  // 营业时间
    sheet.setColumnWidth(6, 130);  // 联系电话
    sheet.setColumnWidth(7, 360);  // 油炸依据

    return ContentService.createTextOutput(JSON.stringify({
      status: "success",
      sheet_name: sheet.getName(),
      sheet_url: ss.getUrl()
    })).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ status: "error", message: err.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}
