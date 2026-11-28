import { themes as prismThemes } from 'prism-react-renderer';
import type { Config } from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';
import type * as Redocusaurus from 'redocusaurus';
import dotenv from 'dotenv';
import fs from 'fs';
import { publicGuideIds } from './sidebars';

const sourceRepository = 'https://github.com/Langboard/langboard';

if (fs.existsSync('../.env')) {
	dotenv.config({ path: '../.env' });
}

const PROJECT_NAME = `${process.env.PROJECT_NAME.charAt(
	0
).toUpperCase()}${process.env.PROJECT_NAME.slice(1)}`;

const downloadOpenApiSpec = async () => {
	try {
		const response = await fetch(process.env.OPENAPI_URL || `${process.env.API_URL}/openapi.json`);
		if (!response.ok) throw new Error(`OpenAPI request failed: ${response.status}`);
		const data = await response.json();
		if (typeof data?.openapi !== 'string' || !data.info || !data.paths) throw new Error('Invalid OpenAPI document');
        if (data.info && data.info.title) {
            data.info.title = 'API';
        }
		fs.writeFileSync(
			'./openapi.json',
			JSON.stringify(data, null, 2),
			'utf-8'
		);
	} catch {
		if (!fs.existsSync('./openapi.json')) {
			console.error("You didn't set API_URL or the API isn't running.");
			process.exit(1);
		}
	}
};

const createConfig = async () => {
	const guidesUrl = process.env.DOCS_PUBLIC_GUIDES_URL;
	if (!guidesUrl) await downloadOpenApiSpec();
	const config: Config = {
		title: PROJECT_NAME,
		tagline: `${PROJECT_NAME} Documentation`,
		favicon: 'img/logo.png',
		url: guidesUrl || process.env.API_URL,
		baseUrl: guidesUrl ? '/docs/' : '/',
		organizationName: 'Yamon',
		projectName: PROJECT_NAME,
		onBrokenLinks: 'throw',
		onBrokenMarkdownLinks: 'warn',

		i18n: {
			defaultLocale: 'en',
			locales: ['en'],
		},

		presets: [
			[
				'classic',
				{
					docs: {
						sidebarPath: './sidebars.ts',
						routeBasePath: guidesUrl ? '/' : 'docs',
						include: publicGuideIds.map((id) => `${id}.md`),
					},
					...(guidesUrl ? { blog: false, pages: false } as const : {}),
					theme: {
						customCss: './src/css/custom.css',
					},
				} satisfies Preset.Options,
			],
		],
		themeConfig: {
			image: 'img/logo.png',
            colorMode: {
                defaultMode: 'dark',
                disableSwitch: false,
                respectPrefersColorScheme: false,
            },
			navbar: {
				title: PROJECT_NAME,
				logo: {
					alt: PROJECT_NAME,
					...(guidesUrl ? { href: `/${publicGuideIds[0]}` } : {}),
					src: 'img/logo.png',
				},
				items: [
					...(!guidesUrl ? [{ to: '/', position: 'left' as const, label: 'API' }] : []),
					{
						type: 'docSidebar',
						sidebarId: 'publicGuides',
						position: 'left',
						label: 'Guides',
					},
					{
						href: sourceRepository,
                        className: "header-github-link",
                        title: "GitHub",
						position: 'right',
					},
				],
			},
			footer: {
				style: 'dark',
				links: [
					{
						title: 'Docs',
						items: [
							{
								to: guidesUrl ? `/${publicGuideIds[0]}` : '/',
								position: 'left',
								label: guidesUrl ? 'Guides' : 'API',
							},
						],
					},
					{
						title: 'More',
						items: [
							{
								label: 'GitHub',
								href: sourceRepository,
							},
						],
					},
				],
				copyright: `Copyright © ${new Date().getFullYear()} ${PROJECT_NAME}.`,
			},
			prism: {
				theme: prismThemes.github,
				darkTheme: prismThemes.dracula,
			},
		} satisfies Preset.ThemeConfig,
	};

	if (!guidesUrl) {
		config.presets?.push([
				'redocusaurus',
				{
					specs: [
						{
							spec: './openapi.json',
							route: '/',
						},
					],
					theme: {
						primaryColor: '#1890ff',
					},
				},
			] satisfies Redocusaurus.PresetEntry);
	}
	return config;
};

export default createConfig;
