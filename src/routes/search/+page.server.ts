import { redirect } from '@sveltejs/kit';
import { docsHref } from '$lib/config/docs-versions';
import type { PageServerLoad } from './$types';

/** Redirect legacy search URLs to the default release while retaining query parameters. */
export const load: PageServerLoad = ({ url }) => {
	redirect(307, docsHref('search') + url.search);
};
