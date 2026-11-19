import { themes as prismThemes } from 'prism-react-renderer';
import type { Config } from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';
import type * as Redocusaurus from 'redocusaurus';
import dotenv from 'dotenv';
import fs from 'fs';

if (fs.existsSync('../.env')) {
	dotenv.config({ path: '../.env' });
}

const PROJECT_NAME = `${process.env.PROJECT_NAME.charAt(
	0
).toUpperCase()}${process.env.PROJECT_NAME.slice(1)}`;

const downloadOpenApiSpec = async () => {
	try {
		const response = await fetch(`${process.env.API_URL}/openapi.json`);
		const data = await response.json();
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
	await downloadOpenApiSpec();
	const config: Config = {
		title: PROJECT_NAME,
		tagline: `${PROJECT_NAME} Documentation`,
		favicon: 'img/logo.png',
		url: process.env.API_URL,
		baseUrl: '/',
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
					},
					theme: {
						customCss: './src/css/custom.css',
					},
				} satisfies Preset.Options,
			],
			[
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
			] satisfies Redocusaurus.PresetEntry,
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
					src: 'img/logo.png',
				},
				items: [
					{
						to: '/',
						position: 'left',
						label: 'API',
					},
					{
						type: 'docSidebar',
						sidebarId: 'publicGuides',
						position: 'left',
						label: 'Guides',
					},
					{
						href: 'https://github.com/yamonco/langboard',
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
								to: '/',
								position: 'left',
								label: 'API',
							},
						],
					},
					{
						title: 'More',
						items: [
							{
								label: 'GitHub',
								href: 'https://github.com/yamonco/langboard',
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

	return config;
};

export default createConfig;
