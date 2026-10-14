import { redirect } from '@sveltejs/kit';
import { docsHref } from '$lib/config/docs-versions';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = ({ url }) => {
	redirect(307, docsHref('search') + url.search);
};
