import { test, expect } from 'playwright/test';

test.describe('配置管理页面（合并后）', () => {
    test.beforeEach(async ({ page }) => {
        await page.goto('http://localhost:8000');
        await page.waitForLoadState('networkidle');
    });

    test('导航栏包含配置管理按钮，不包含环境管理按钮', async ({ page }) => {
        // 应该有配置管理按钮
        const configBtn = page.locator('button', { hasText: '配置管理' });
        await expect(configBtn).toBeVisible();

        // 不应该有环境管理按钮
        const envBtn = page.locator('button', { hasText: '环境管理' });
        await expect(envBtn).toHaveCount(0);
    });

    test('点击配置管理进入页面', async ({ page }) => {
        await page.click('button:has-text("配置管理")');
        await page.waitForTimeout(500);

        // 页面应该可见
        const pageData = page.locator('#page-data');
        await expect(pageData).toBeVisible();
    });

    test('配置管理页面包含三个区域：全局变量、CSV数据、环境配置', async ({ page }) => {
        await page.click('button:has-text("配置管理")');
        await page.waitForTimeout(1000);

        // 检查全局变量区域
        await expect(page.locator('h3:has-text("全局变量")')).toBeVisible();

        // 检查CSV数据文件区域
        await expect(page.locator('h3:has-text("CSV 数据文件")')).toBeVisible();

        // 检查环境配置区域
        await expect(page.locator('h3:has-text("环境配置")')).toBeVisible();
    });

    test('环境配置区域有新建环境按钮', async ({ page }) => {
        await page.click('button:has-text("配置管理")');
        await page.waitForTimeout(1000);

        const createEnvBtn = page.locator('button:has-text("新建环境")');
        await expect(createEnvBtn).toBeVisible();
    });

    test('点击新建环境按钮弹出弹窗', async ({ page }) => {
        await page.click('button:has-text("配置管理")');
        await page.waitForTimeout(1000);

        await page.click('button:has-text("新建环境")');
        await page.waitForTimeout(500);

        // 弹窗应该可见
        const modal = page.locator('#env-create-modal');
        await expect(modal).toBeVisible();

        // 弹窗应该包含表单字段
        await expect(page.locator('#env-name')).toBeVisible();
        await expect(page.locator('#env-url')).toBeVisible();
        await expect(page.locator('#env-desc')).toBeVisible();
    });

    test('其他页面正常显示（无布局问题）', async ({ page }) => {
        // 测试任务管理页面
        await page.click('button:has-text("任务管理")');
        await page.waitForTimeout(500);
        await expect(page.locator('#page-tasks')).toBeVisible();

        // 测试脚本管理页面
        await page.click('button:has-text("脚本管理")');
        await page.waitForTimeout(500);
        await expect(page.locator('#page-scripts')).toBeVisible();

        // 测试模板管理页面
        await page.click('button:has-text("模板管理")');
        await page.waitForTimeout(500);
        await expect(page.locator('#page-templates')).toBeVisible();
    });
});
