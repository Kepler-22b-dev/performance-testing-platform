import { test, expect, type Page } from 'playwright/test';

const BASE_URL = 'http://localhost:8000';

test.describe('性能测试平台 - 主流程测试', () => {

    test.describe('1. 首页', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.waitForLoadState('networkidle');
        });

        test('首页正常加载', async ({ page }) => {
            await expect(page.locator('#page-home')).toBeVisible();
            await expect(page.locator('h1:has-text("性能工程平台")')).toBeVisible();
        });

        test('导航栏按钮完整', async ({ page }) => {
            const navBar = page.locator('.header .nav');
            const buttons = ['首页', '任务管理', '创建任务', '配置管理', '施压节点', '任务对比', 'JTL 导入', '模板管理', '定时调度', '通知设置', '性能趋势', '告警规则', '工具日志', '移动端监控', '脚本管理'];
            for (const btn of buttons) {
                await expect(navBar.locator(`button:has-text("${btn}")`).first()).toBeVisible();
            }
        });

        test('首页数据卡片显示', async ({ page }) => {
            await expect(page.locator('.home-value-card')).toHaveCount(4);
        });
    });

    test.describe('2. 任务管理', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("任务管理")');
            await page.waitForTimeout(1000);
        });

        test('任务列表页面正常', async ({ page }) => {
            await expect(page.locator('#page-tasks')).toBeVisible();
        });

        test('任务操作按钮存在', async ({ page }) => {
            await expect(page.locator('#page-tasks .toolbar-actions button').first()).toBeVisible();
        });
    });

    test.describe('3. 创建任务', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("创建任务")');
            await page.waitForTimeout(1000);
        });

        test('创建任务页面正常', async ({ page }) => {
            await expect(page.locator('#page-create')).toBeVisible();
        });

        test('新手引导流程显示', async ({ page }) => {
            await expect(page.locator('.create-guide-title:has-text("新手启动流程")')).toBeVisible();
        });
    });

    test.describe('4. 配置管理', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("配置管理")');
            await page.waitForTimeout(1000);
        });

        test('配置管理页面包含三个区域', async ({ page }) => {
            await expect(page.locator('h3:has-text("全局变量")')).toBeVisible();
            await expect(page.locator('h3:has-text("CSV 数据文件")')).toBeVisible();
            await expect(page.locator('h3:has-text("环境配置")')).toBeVisible();
        });

        test('全局变量添加功能', async ({ page }) => {
            const varName = `test_${Date.now()}`;
            await page.fill('#var-name', varName);
            await page.fill('#var-value', 'test_value');
            await page.fill('#var-desc', '测试变量');
            await page.locator('#page-data button:has-text("添加")').click();
            await page.waitForTimeout(500);
            // 应该在表格中显示
            await expect(page.locator(`td:has-text("${varName}")`)).toBeVisible();
        });

        test('环境配置新建弹窗', async ({ page }) => {
            await page.locator('button:has-text("新建环境")').click();
            await page.waitForTimeout(500);
            await expect(page.locator('#env-create-modal')).toBeVisible();
            await expect(page.locator('#env-name')).toBeVisible();
            await page.locator('#env-create-modal button:has-text("取消")').click();
        });
    });

    test.describe('5. 施压节点', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("施压节点")');
            await page.waitForTimeout(1000);
        });

        test('施压节点页面正常', async ({ page }) => {
            await expect(page.locator('#page-nodes')).toBeVisible();
        });

        test('节点列表显示', async ({ page }) => {
            await expect(page.locator('#page-nodes h3').first()).toBeVisible();
        });
    });

    test.describe('6. 模板管理', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("模板管理")');
            await page.waitForTimeout(1000);
        });

        test('模板管理页面正常', async ({ page }) => {
            await expect(page.locator('#page-templates')).toBeVisible();
        });

        test('新建模板按钮存在', async ({ page }) => {
            await expect(page.locator('button:has-text("新建模板")')).toBeVisible();
        });
    });

    test.describe('7. 脚本管理', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("脚本管理")');
            await page.waitForTimeout(1000);
        });

        test('脚本管理页面正常', async ({ page }) => {
            await expect(page.locator('#page-scripts')).toBeVisible();
        });

        test('上传脚本按钮存在', async ({ page }) => {
            await expect(page.locator('#page-scripts button:has-text("上传脚本")')).toBeVisible();
        });

        test('搜索功能存在', async ({ page }) => {
            await expect(page.locator('#script-search')).toBeVisible();
        });
    });

    test.describe('8. 告警规则', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("告警规则")');
            await page.waitForTimeout(1000);
        });

        test('告警规则页面正常', async ({ page }) => {
            await expect(page.locator('#page-alerts')).toBeVisible();
        });

        test('新建规则按钮存在', async ({ page }) => {
            await expect(page.locator('button:has-text("新建规则")')).toBeVisible();
        });
    });

    test.describe('9. 工具日志', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("工具日志")');
            await page.waitForTimeout(1000);
        });

        test('工具日志页面正常', async ({ page }) => {
            await expect(page.locator('#page-tool-logs')).toBeVisible();
        });

        test('日志类型选择存在', async ({ page }) => {
            await expect(page.locator('#tool-log-lines')).toBeVisible();
        });
    });

    test.describe('10. 移动端监控', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("移动端监控")');
            await page.waitForTimeout(2000);
        });

        test('移动端监控页面正常', async ({ page }) => {
            await expect(page.locator('#page-mobile')).toBeVisible();
        });

        test('平台选择器存在', async ({ page }) => {
            await expect(page.locator('#mobile-platform')).toBeVisible();
        });

        test('设备选择器存在', async ({ page }) => {
            await expect(page.locator('#mobile-device')).toBeVisible();
        });

        test('应用选择器存在', async ({ page }) => {
            await expect(page.locator('#mobile-app')).toBeVisible();
        });

        test('开始监控按钮存在', async ({ page }) => {
            await expect(page.locator('#btn-mobile-start')).toBeVisible();
        });

        test('性能数据卡片显示', async ({ page }) => {
            await expect(page.locator('#mobile-cpu')).toBeVisible();
            await expect(page.locator('#mobile-memory')).toBeVisible();
            await expect(page.locator('#mobile-pid')).toBeVisible();
        });

        test('图表容器存在', async ({ page }) => {
            await expect(page.locator('#mobile-chart-cpu')).toBeVisible();
            await expect(page.locator('#mobile-chart-fps')).toBeVisible();
        });

        test('保存图片按钮存在', async ({ page }) => {
            await expect(page.locator('button:has-text("保存图片")')).toHaveCount(2);
        });
    });

    test.describe('11. 性能趋势', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("性能趋势")');
            await page.waitForTimeout(1000);
        });

        test('性能趋势页面正常', async ({ page }) => {
            await expect(page.locator('#page-trend')).toBeVisible();
        });

        test('接口选择器存在', async ({ page }) => {
            await expect(page.locator('#trend-label')).toBeVisible();
        });
    });

    test.describe('12. 任务对比', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("任务对比")');
            await page.waitForTimeout(1000);
        });

        test('任务对比页面正常', async ({ page }) => {
            await expect(page.locator('#page-compare')).toBeVisible();
        });

        test('对比按钮存在', async ({ page }) => {
            await expect(page.locator('#compare-run-btn')).toBeVisible();
        });
    });

    test.describe('13. JTL 导入', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("JTL 导入")');
            await page.waitForTimeout(1000);
        });

        test('JTL 导入页面正常', async ({ page }) => {
            await expect(page.locator('#page-jtl')).toBeVisible();
        });

        test('上传按钮存在', async ({ page }) => {
            await expect(page.locator('button:has-text("上传 JTL 文件")')).toBeVisible();
        });
    });

    test.describe('14. 定时调度', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("定时调度")');
            await page.waitForTimeout(1000);
        });

        test('定时调度页面正常', async ({ page }) => {
            await expect(page.locator('#page-schedule')).toBeVisible();
        });
    });

    test.describe('15. 通知设置', () => {
        test.beforeEach(async ({ page }) => {
            await page.goto(BASE_URL);
            await page.click('button:has-text("通知设置")');
            await page.waitForTimeout(1000);
        });

        test('通知设置页面正常', async ({ page }) => {
            await expect(page.locator('#page-notify')).toBeVisible();
        });
    });

    test.describe('16. API 健康检查', () => {
        test('健康检查接口正常', async ({ request }) => {
            const response = await request.get(`${BASE_URL}/api/health`);
            expect(response.ok()).toBeTruthy();
            const data = await response.json();
            expect(data.status).toBe('ok');
        });

        test('移动端检测接口正常', async ({ request }) => {
            const response = await request.get(`${BASE_URL}/api/mobile/detect`);
            expect(response.ok()).toBeTruthy();
        });

        test('移动端应用列表接口正常', async ({ request }) => {
            const response = await request.get(`${BASE_URL}/api/mobile/apps/ios`);
            expect(response.ok()).toBeTruthy();
        });
    });
});
