import { redirect } from '@sveltejs/kit';
import { docsHref } from '$lib/config/docs-versions';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = ({ params, url }) => {
	redirect(307, docsHref(`${params.section}${params.slug ? `/${params.slug}` : ''}`) + url.search);
};
