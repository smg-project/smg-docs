import { redirect } from '@sveltejs/kit';
import { docsHref } from '$lib/config/docs-versions';
import type { PageServerLoad } from './$types';

/** Send a version root to its getting-started page while retaining query parameters. */
export const load: PageServerLoad = ({ params, url }) => {
	redirect(307, docsHref('getting-started', params.version) + url.search);
};
