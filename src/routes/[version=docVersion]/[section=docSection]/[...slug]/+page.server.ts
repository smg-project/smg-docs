import { loadDocPage } from '$lib/server/docs';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = ({ params }) =>
	loadDocPage(params.version, params.section, params.slug);
