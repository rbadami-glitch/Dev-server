import { describe, expect, it } from 'vitest';
import { cwPayResultPdfBlob } from './cwPayPdfExport';

describe('CW Pay PDF export', () => {
	it('creates a PDF for text and a payment-result table', async () => {
		const blob = await cwPayResultPdfBlob(
			'# Payment status\n\nThe invoice is paid.\n\n| Invoice | Status |\n| --- | --- |\n| 0096101IN | Paid |'
		);
		const bytes = new Uint8Array(await blob.arrayBuffer());
		expect(new TextDecoder().decode(bytes.slice(0, 5))).toBe('%PDF-');
	});
});


