// SPDX-License-Identifier: GPL-2.0
/*
 * dfr-probe v3 — draw to the Apple T1 iBridge Touch Bar.
 *
 * The DFR wire protocol implemented here is derived from DFRDisplayKm, the
 * Windows Touch Bar driver by imbushuo (MIT), vendored in this repo under
 * reference/DFRDisplayKm. Specifically: the request/response envelope, the
 * framebuffer update layout and its field values, the FourCC keys, the D0-entry
 * bring-up order, and dfr_update_padding[] below, which is copied byte-for-byte
 * from DfrUpdatePadding[] in src/DFRDisplayKm/DfrDisplay.c. MIT is compatible
 * with the GPL-2.0 this module ships under; the original notice is retained in
 * reference/DFRDisplayKm/LICENSE.
 *
 * Confirmed by v2: REDY -> GINF returns width=2170 height=60 pixelFormat="ABGR".
 * v3 adds a framebuffer update after the info reply.
 *
 * Update request = standard 32-byte envelope with a payload at 0x20:
 *   u32 FrameID, BeginX, BeginY, Width, Height, BufferSize      (24 bytes)
 * length fields (0x0C and 0x1C) = 0x10 + payload = 0x28.
 * Pixel data follows as a separate bulk OUT transfer.
 */
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/usb.h>
#include <linux/workqueue.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/uaccess.h>

#define DFR_REQ_HEADER   0x15120002u
#define DFR_REQ_HEADER_UDCL 0x01120002u  /* macOS uses flags 0x112 for UDCL only */
#define DFR_FB_HEADER       0x00120002u  /* DFR_DEVICE_UPDATE_FB_REQUEST_HEADER */
#define DFR_RESP_HEADER  0x01140000u
#define DFR_KEY_GINF     0x47494e46u
#define DFR_KEY_REDY     0x52454459u
#define DFR_KEY_UDCL     0x5544434cu
#define DFR_KEY_WDSP     0x57445350u
#define DFR_KEY_SORI     0x534f5249u   /* set orientation */
#define DFR_KEY_KBMC     0x4b424d43u   /* keyboard mode C */
#define DFR_KEY_KBMD     0x4b424d44u   /* keyboard mode D */
#define DFR_KEY_SBTN     0x5342544eu   /* set button      */
#define DFR_KEY_RUSO     0x5255534fu   /* resume?         */
#define DFR_KEY_CLDR     0x434c4452u   /* DFR_CLEAR_SCREEN_KEY */

#define DFR_HDR_LEN      32
#define DFR_RESP_LEN     512

static int rect_w = 2170;   module_param(rect_w, int, 0644);  /* full panel width */
static int bpp    = 3;      module_param(bpp, int, 0644);
/* Default fill is BLACK: the module starts its frame loop as soon as it loads
 * (that is what holds the display session open), a second or two before the UI
 * starts writing real frames. A non-black default flashes that colour on every
 * start. */
static int colr   = 0x00;   module_param(colr, int, 0644);
static int colg   = 0x00;   module_param(colg, int, 0644);
static int colb   = 0x00;   module_param(colb, int, 0644);
static uint key   = DFR_KEY_WDSP; module_param(key, uint, 0644);
static int order  = 0;   module_param(order, int, 0644);  /* 0=RGB 1=BGR */
static int period = 1;   module_param(period, int, 0644);  /* keep redrawing */
static int draw_en = 1;  module_param(draw_en, int, 0644); /* 0 = handshake only, never draw */
static int padlen = 88;  module_param(padlen, int, 0644); /* only used by fbmode=0; fbmode=1 uses the real 88-byte table */
static int fb_ep = 0;    module_param(fb_ep, int, 0644);   /* 0 = use if2.3 OUT (0x02); else raw EP addr e.g. 0x05 */
/* fbmode 1 = DFR_UPDATE_FB_REQUEST - the ONLY one that renders. 0 = the old
 * WDSP experiment, kept for reference; it ACKs but never paints. */
static int fbmode = 1;   module_param(fbmode, int, 0644);
static int split = 0;    module_param(split, int, 0644);   /* 1 = geometry pkt, then pixels as a 2nd transfer */
static int extra = 0;    module_param(extra, int, 0644);   /* 1 = send SORI/KBMC/KBMD/SBTN/RUSO after GINF */
/* Per-packet tracing. OFF by default: the frame loop runs at ~30fps forever, so
 * logging every request/response with hex dumps writes GIGABYTES to the journal
 * in hours and rotates away all other history. Only turn this on to debug the
 * protocol, and turn it off again. */
static int verbose = 0;  module_param(verbose, int, 0644);

static void *fb_buf;             /* preallocated at module load */
static u8 *user_fb;              /* pixels pushed from /dev/dfr0 */
static bool user_fb_valid;
static DEFINE_SPINLOCK(user_fb_lock);   /* draw() runs in URB completion (atomic) - MUST NOT be a mutex */
#define PANEL_W 2170
#define PANEL_H 60
#define PANEL_BPP 3
#define PANEL_FB_BYTES (PANEL_W * PANEL_H * PANEL_BPP)
#define FB_MAX (512 * 1024)


/* Copied byte-for-byte from DfrUpdatePadding[] in DFRDisplayKm (imbushuo, MIT),
 * reference/DFRDisplayKm/src/DFRDisplayKm/DfrDisplay.c. Not zeros and not 96
 * bytes: both of those were wrong guesses that cost real time. Do not "tidy".
 */
static const u8 dfr_update_padding[88] = {
	0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
	0x00,0x00,0x00,0x00,0xFE,0xFF,0x00,0x00,
	0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
	0x00,0x00,0x00,0x00,0x01,0x00,0x08,0x00,
	0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
	0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
	0x00,0x00,0x00,0x00,0x02,0x00,0x08,0x00,
	0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
	0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
	0x00,0x00,0x00,0x00,0xFF,0xFF,0x00,0x00,
	0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00
};

struct dfr_ctx {
	struct usb_device *udev;
	__u8 ep_in, ep_out;
	struct urb *in_urb;
	void *in_buf;
	struct urb *in_pool[8];
	void *in_pbuf[8];
	bool sent_ginf, drew, stop, inited, got_info;
	u32 w, h, frame;
	struct work_struct start_work;
	/* Every outbound URB is anchored so suspend can wait for the in-flight
	 * ones to finish. The frame loop resubmits from its own completion
	 * handler, so without this there is no point at which the traffic is
	 * known to have stopped. */
	struct usb_anchor tx_anchor;
};

static void draw(struct dfr_ctx *ctx);
static int send_cmd(struct dfr_ctx *ctx, u32 k, const u32 *payload, int npay);

static void tx_done(struct urb *urb)
{
	if (urb->status)
		pr_warn_ratelimited("dfr-probe: TX status %d (len %d)\n",
				    urb->status, urb->actual_length);
	usb_free_urb(urb);
}

/* completion for the framebuffer urb: immediately queue the next frame so the
 * T1 keeps seeing traffic and holds the display session open */
static void frame_done(struct urb *urb)
{
	struct dfr_ctx *ctx = urb->context;
	int st = urb->status;

	usb_free_urb(urb);
	if (st) {
		pr_warn_ratelimited("dfr-probe: frame TX status %d\n", st);
		return;
	}
	if (ctx && !ctx->stop) {
		/* commit the frame: UDCL acknowledgement (uses its own header) */
		send_cmd(ctx, DFR_KEY_UDCL, NULL, 0);
		if (period)
			draw(ctx);
	}
}

/* send a bulk buffer that we own; buffer freed by URB_FREE_BUFFER */
static int tx_raw(struct dfr_ctx *ctx, void *buf, int len, bool free_buf)
{
	struct urb *urb = usb_alloc_urb(0, GFP_ATOMIC);
	int rc;

	if (!urb)
		return -ENOMEM;
	usb_fill_bulk_urb(urb, ctx->udev, usb_sndbulkpipe(ctx->udev, ctx->ep_out),
			  buf, len, tx_done, ctx);
	if (free_buf)
		urb->transfer_flags |= URB_FREE_BUFFER;
	usb_anchor_urb(urb, &ctx->tx_anchor);
	rc = usb_submit_urb(urb, GFP_ATOMIC);
	if (rc) {
		usb_unanchor_urb(urb);
		usb_free_urb(urb);
	}
	return rc;
}

static int send_cmd(struct dfr_ctx *ctx, u32 k, const u32 *payload, int npay)
{
	int content = 0x10 + npay * 4;
	int total = 16 + content;
	__le32 *p = kzalloc(total, GFP_ATOMIC);
	int i, rc;

	if (!p)
		return -ENOMEM;
	p[0] = cpu_to_le32(k == DFR_KEY_UDCL ? DFR_REQ_HEADER_UDCL : DFR_REQ_HEADER);
	p[3] = cpu_to_le32(content);
	p[4] = cpu_to_le32(k);
	p[7] = cpu_to_le32(content);
	for (i = 0; i < npay; i++)
		p[8 + i] = cpu_to_le32(payload[i]);
	rc = tx_raw(ctx, p, total, true);
	if (verbose)
		pr_info("dfr-probe: >>> %c%c%c%c payload=%d total=%d rc=%d\n",
			(k >> 24) & 0xff, (k >> 16) & 0xff, (k >> 8) & 0xff,
			k & 0xff, npay, total, rc);
	if (rc)
		kfree(p);
	return rc;
}

static void draw(struct dfr_ctx *ctx)
{
	/* DFR_UPDATE_FB_REQUEST — its own header constant, no FourCC key.
	 * 16-byte generic header + 44-byte packed content, pixels follow. */
	u32 w = rect_w, h = ctx->h ? ctx->h : 60;
	u32 nbytes = w * h * bpp;
	u32 content = 44;
	u32 pad = padlen;                /* DfrUpdatePadding: Windows appends 96 bytes */
	u32 total = 16 + content + nbytes + pad;
	/* recomputed below for fbmode 0 */
	u8 *b = fb_buf;
	u8 *px;
	u32 i, rc;

	if (total > FB_MAX) {
		pr_info("dfr-probe: frame too big (%u)\n", total);
		return;
	}
	if (fbmode == 0) {
		/* The variant that produced a visible flash on 2026-08-17 21:13:
		 * FourCC key WDSP, u32 geometry at 0x20, pixels inline at 0x38. */
		u32 c2 = 0x10 + 24 + nbytes;
		__le32 *p32 = (__le32 *)b;

		memset(b, 0, 16 + 0x10 + 24);
		p32[0] = cpu_to_le32(DFR_REQ_HEADER);
		p32[3] = cpu_to_le32(c2);
		p32[4] = cpu_to_le32(DFR_KEY_WDSP);
		p32[7] = cpu_to_le32(c2);
		p32[8]  = cpu_to_le32(++ctx->frame);
		p32[9]  = cpu_to_le32(0);
		p32[10] = cpu_to_le32(0);
		p32[11] = cpu_to_le32(w);
		p32[12] = cpu_to_le32(h);
		p32[13] = cpu_to_le32(nbytes);
		px = b + 16 + 0x10 + 24;
		goto fill;
	}
	/* EXACT DFRDisplayKm layout:
	 *   RequestBufferLength = fb + sizeof(DFR_UPDATE_FB_REQUEST=60) + sizeof(padding=88)
	 *   Header.Reserved1    = 0x09
	 *   Header.RequestLength= RequestBufferLength - 16
	 *   Content.Reserved0   = 0x0001
	 */
	pad = sizeof(dfr_update_padding);
	total = 16 + content + nbytes + pad;
	memset(b, 0, 16 + content);
	*(__le32 *)(b + 0x00) = cpu_to_le32(DFR_FB_HEADER);
	*(__le32 *)(b + 0x04) = cpu_to_le32(0x00000009);
	*(__le32 *)(b + 0x0c) = cpu_to_le32(total - 16);
	*(__le16 *)(b + 0x10) = cpu_to_le16(0x0001);
	b[0x12] = (u8)(++ctx->frame ? ctx->frame : ++ctx->frame);
	*(__le16 *)(b + 0x30) = cpu_to_le16(0);
	*(__le16 *)(b + 0x32) = cpu_to_le16(0);
	*(__le16 *)(b + 0x34) = cpu_to_le16((u16)w);
	*(__le16 *)(b + 0x36) = cpu_to_le16((u16)h);
	*(__le32 *)(b + 0x38) = cpu_to_le32(nbytes);
	memcpy(b + 16 + content + nbytes, dfr_update_padding, pad);
	px = b + 16 + content;
fill:
	if (user_fb_valid && bpp == PANEL_BPP &&
	    w * h * bpp <= PANEL_FB_BYTES) {
		{
			unsigned long flags;

			spin_lock_irqsave(&user_fb_lock, flags);
			memcpy(px, user_fb, w * h * bpp);
			spin_unlock_irqrestore(&user_fb_lock, flags);
		}
		goto sent;
	}
	for (i = 0; i < w * h; i++) {
		if (order) {
			px[i * bpp + 0] = colb;
			px[i * bpp + 1] = colg;
			px[i * bpp + 2] = colr;
		} else {
			px[i * bpp + 0] = colr;
			px[i * bpp + 1] = colg;
			px[i * bpp + 2] = colb;
		}
		if (bpp == 4)
			px[i * bpp + 3] = 0xff;
	}

	if (fbmode == 0)
		total = 16 + 0x10 + 24 + nbytes;
sent:
	{
		struct urb *u = usb_alloc_urb(0, GFP_ATOMIC);
		u32 reqlen = 16 + content;

		if (!u)
			return;
		if (split) {
			usb_fill_bulk_urb(u, ctx->udev,
					  usb_sndbulkpipe(ctx->udev, ctx->ep_out),
					  fb_buf, reqlen, tx_done, ctx);
			usb_anchor_urb(u, &ctx->tx_anchor);
			rc = usb_submit_urb(u, GFP_ATOMIC);
			if (rc) { usb_unanchor_urb(u); usb_free_urb(u); return; }
			u = usb_alloc_urb(0, GFP_ATOMIC);
			if (!u)
				return;
			usb_fill_bulk_urb(u, ctx->udev,
					  usb_sndbulkpipe(ctx->udev, ctx->ep_out),
					  (u8 *)fb_buf + reqlen, nbytes,
					  frame_done, ctx);
		} else {
			usb_fill_bulk_urb(u, ctx->udev,
					  usb_sndbulkpipe(ctx->udev, fb_ep ? fb_ep : ctx->ep_out),
					  fb_buf, total, frame_done, ctx);
		}
		usb_anchor_urb(u, &ctx->tx_anchor);
		rc = usb_submit_urb(u, GFP_ATOMIC);
		if (rc) {
			usb_unanchor_urb(u);
			usb_free_urb(u);
		}
	}
	if (!ctx->drew)
		pr_info("dfr-probe: DRAW %ux%u bpp=%d order=%d px=%u pad=%u total=%u hdr=0x%08x rc=%d\n",
			w, h, bpp, order, nbytes, pad, total, DFR_FB_HEADER, rc);
	ctx->drew = true;
}

static void in_done(struct urb *urb)
{
	struct dfr_ctx *ctx = urb->context;
	u8 *b = urb->transfer_buffer;
	int n = urb->actual_length;
	u32 hdr, k;

	if (urb->status) {
		pr_warn_ratelimited("dfr-probe: IN status %d\n", urb->status);
		return;
	}
	if (n < 4)
		goto again;
	hdr = le32_to_cpup((__le32 *)b);
	if (hdr == DFR_REQ_HEADER)
		goto again;                      /* echo */
	if (hdr & 0x80000000u) {
		if (verbose) {
			pr_info("dfr-probe: <<< ACK/response hdr=0x%08x len=%d\n",
				hdr, n);
			print_hex_dump(KERN_INFO, "dfr-probe: ack ",
				       DUMP_PREFIX_OFFSET, 16, 1, b,
				       min(n, 64), false);
		}
		goto again;
	}

	k = (n >= 20) ? le32_to_cpup((__le32 *)(b + 0x10)) : 0;
	if (verbose) {
		pr_info("dfr-probe: <<< %d bytes key=%c%c%c%c\n", n,
			(k >> 24) & 0xff, (k >> 16) & 0xff, (k >> 8) & 0xff,
			k & 0xff);
		print_hex_dump(KERN_INFO, "dfr-probe: ", DUMP_PREFIX_OFFSET,
			       16, 1, b, min(n, 80), false);
	}

	if (k == DFR_KEY_GINF && n >= 0x28) {
		ctx->w = le32_to_cpup((__le32 *)(b + 0x20));
		ctx->h = le32_to_cpup((__le32 *)(b + 0x24));
		pr_info("dfr-probe: panel %ux%u\n", ctx->w, ctx->h);
		ctx->got_info = true;
		if (!ctx->inited) {
			ctx->inited = true;
			send_cmd(ctx, DFR_KEY_REDY, NULL, 0);   /* host ready */
			send_cmd(ctx, DFR_KEY_CLDR, NULL, 0);   /* clear screen */
		}
		if (extra && !ctx->drew) {
			send_cmd(ctx, DFR_KEY_SORI, NULL, 0);
			send_cmd(ctx, DFR_KEY_KBMD, NULL, 0);
			send_cmd(ctx, DFR_KEY_KBMC, NULL, 0);
			send_cmd(ctx, DFR_KEY_SBTN, NULL, 0);
			send_cmd(ctx, DFR_KEY_RUSO, NULL, 0);
		}
		if (draw_en && !ctx->drew)
			draw(ctx);
	}
	if (!ctx->got_info && k != DFR_KEY_GINF) {
		send_cmd(ctx, DFR_KEY_GINF, NULL, 0);   /* retry until info arrives */
	}
again:
	usb_submit_urb(urb, GFP_ATOMIC);
}

static void dfr_start_work(struct work_struct *w)
{
	struct dfr_ctx *ctx = container_of(w, struct dfr_ctx, start_work);
	struct usb_device *udev = ctx->udev;
	int rc;

	if (ctx->stop)
		return;
	usb_clear_halt(udev, usb_rcvbulkpipe(udev, ctx->ep_in));
	usb_clear_halt(udev, usb_sndbulkpipe(udev, ctx->ep_out));

	usb_fill_bulk_urb(ctx->in_urb, udev, usb_rcvbulkpipe(udev, ctx->ep_in),
			  ctx->in_buf, DFR_RESP_LEN, in_done, ctx);
	rc = usb_submit_urb(ctx->in_urb, GFP_KERNEL);
	pr_info("dfr-probe: (deferred) IN armed %d, starting handshake\n", rc);
	send_cmd(ctx, DFR_KEY_GINF, NULL, 0);
}

static int dfr_probe(struct usb_interface *intf, const struct usb_device_id *id)
{
	struct usb_device *udev = interface_to_usbdev(intf);
	struct usb_host_interface *alt = intf->cur_altsetting;

	/* NEVER interfere outside config 2 — config 1 belongs to apple-ibridge. */
	if (!udev->actconfig ||
	    udev->actconfig->desc.bConfigurationValue != 2)
		return -ENODEV;

	/* Claim every non-DFR interface immediately. The HID interfaces (2.2, 2.6)
	 * otherwise take ~50ms to come up via usbhid -> apple-ibridge-hid, and the
	 * T1 abandons config 2 at ~30ms with them still unclaimed. */
	if (alt->desc.bInterfaceClass != 0x10) {
		dev_info(&intf->dev, "dfr-probe: stub-claiming interface %d (class 0x%02x)\n",
			 alt->desc.bInterfaceNumber, alt->desc.bInterfaceClass);
		usb_set_intfdata(intf, NULL);
		return 0;
	}
	struct dfr_ctx *ctx;
	int i, rc;

	dev_info(&intf->dev, "dfr-probe v3: claimed DFR interface\n");
	ctx = kzalloc(sizeof(*ctx), GFP_ATOMIC);
	if (!ctx)
		return -ENOMEM;
	ctx->udev = udev;
	for (i = 0; i < alt->desc.bNumEndpoints; i++) {
		struct usb_endpoint_descriptor *ep = &alt->endpoint[i].desc;

		if (usb_endpoint_is_bulk_in(ep))
			ctx->ep_in = ep->bEndpointAddress;
		else if (usb_endpoint_is_bulk_out(ep))
			ctx->ep_out = ep->bEndpointAddress;
	}
	ctx->in_buf = kmalloc(DFR_RESP_LEN, GFP_ATOMIC);
	ctx->in_urb = usb_alloc_urb(0, GFP_ATOMIC);
	if (!ctx->in_buf || !ctx->in_urb || !ctx->ep_in || !ctx->ep_out) {
		kfree(ctx->in_buf); usb_free_urb(ctx->in_urb); kfree(ctx);
		return -ENODEV;
	}
	usb_set_intfdata(intf, ctx);
	/* Do NOT touch the bus from probe(): usbcore is still adding interfaces and
	 * submitting URBs here provokes "EP not empty, refuse reset" and the whole
	 * configuration gets torn down. Defer everything to a work item. */
	init_usb_anchor(&ctx->tx_anchor);
	INIT_WORK(&ctx->start_work, dfr_start_work);
	schedule_work(&ctx->start_work);
	rc = 0;
	{
		int j;

		for (j = 0; j < 8; j++) {
			ctx->in_pbuf[j] = kmalloc(DFR_RESP_LEN, GFP_ATOMIC);
			ctx->in_pool[j] = usb_alloc_urb(0, GFP_ATOMIC);
			if (!ctx->in_pbuf[j] || !ctx->in_pool[j])
				break;
			usb_fill_bulk_urb(ctx->in_pool[j], udev,
					  usb_rcvbulkpipe(udev, ctx->ep_in),
					  ctx->in_pbuf[j], DFR_RESP_LEN,
					  in_done, ctx);
			usb_submit_urb(ctx->in_pool[j], GFP_ATOMIC);
		}
		dev_info(&intf->dev, "dfr-probe: %d extra IN urbs queued\n", j);
	}
	send_cmd(ctx, DFR_KEY_REDY, NULL, 0);
	return 0;
}

static void dfr_disconnect(struct usb_interface *intf)
{
	struct dfr_ctx *ctx = usb_get_intfdata(intf);

	if (!ctx)
		return;                 /* stub-claimed interface */
	pr_info("dfr-probe: disconnect (drew=%d)\n", ctx->drew);
	ctx->stop = true;
	cancel_work_sync(&ctx->start_work);
	usb_kill_anchored_urbs(&ctx->tx_anchor);
	{
		int j;

		for (j = 0; j < 8; j++) {
			if (ctx->in_pool[j]) {
				usb_kill_urb(ctx->in_pool[j]);
				usb_free_urb(ctx->in_pool[j]);
			}
			kfree(ctx->in_pbuf[j]);
		}
	}
	usb_kill_urb(ctx->in_urb);
	usb_free_urb(ctx->in_urb);
	kfree(ctx->in_buf);
	kfree(ctx);
	usb_set_intfdata(intf, NULL);
}

static ssize_t dfr_dev_write(struct file *f, const char __user *ubuf,
			     size_t len, loff_t *off)
{
	size_t n = min_t(size_t, len, PANEL_FB_BYTES);

	if (!user_fb)
		return -ENOMEM;
	/* copy_from_user can sleep, so stage outside the lock */
	{
		unsigned long flags;
		u8 *staging = kmalloc(PANEL_FB_BYTES, GFP_KERNEL);

		if (!staging)
			return -ENOMEM;
		if (copy_from_user(staging, ubuf, n)) {
			kfree(staging);
			return -EFAULT;
		}
		if (n < PANEL_FB_BYTES)
			memset(staging + n, 0, PANEL_FB_BYTES - n);
		spin_lock_irqsave(&user_fb_lock, flags);
		memcpy(user_fb, staging, PANEL_FB_BYTES);
		user_fb_valid = true;
		spin_unlock_irqrestore(&user_fb_lock, flags);
		kfree(staging);
	}
	return len;
}

static const struct file_operations dfr_dev_fops = {
	.owner = THIS_MODULE,
	.write = dfr_dev_write,
	.llseek = noop_llseek,
};

static struct miscdevice dfr_misc = {
	.minor = MISC_DYNAMIC_MINOR,
	.name  = "dfr0",
	.fops  = &dfr_dev_fops,
	.mode  = 0666,
};

static const struct usb_device_id dfr_ids[] = {
	{ USB_DEVICE_AND_INTERFACE_INFO(0x05ac, 0x8600, 0x10, 0x00, 0x00) },  /* DFR display */
	{ USB_DEVICE_AND_INTERFACE_INFO(0x05ac, 0x8600, 0x02, 0x0d, 0x00) },  /* CDC-NCM ctrl */
	{ USB_DEVICE_AND_INTERFACE_INFO(0x05ac, 0x8600, 0x0a, 0x00, 0x01) },  /* CDC data    */
	{ USB_DEVICE_AND_INTERFACE_INFO(0x05ac, 0x8600, 0x03, 0x00, 0x01) },  /* HID         */
	{ USB_DEVICE_AND_INTERFACE_INFO(0x05ac, 0x8600, 0x03, 0x01, 0x01) },  /* HID boot kbd*/
	{ USB_DEVICE_AND_INTERFACE_INFO(0x05ac, 0x8600, 0x0e, 0x01, 0x00) },  /* video ctrl  */
	{ USB_DEVICE_AND_INTERFACE_INFO(0x05ac, 0x8600, 0x0e, 0x02, 0x00) },  /* video strm  */
	{ }
};
MODULE_DEVICE_TABLE(usb, dfr_ids);

/* System suspend. The frame loop is self-perpetuating - frame_done() queues
 * the next frame from the completion handler - so unless it is explicitly shut
 * down the driver keeps submitting URBs while the USB core is trying to
 * quiesce the device. That wedges the whole suspend: the machine stops at
 * "PM: suspend entry" and never comes back, taking the display and input
 * devices with it. Stop the loop, then wait for what is already on the wire.
 */
static int dfr_suspend(struct usb_interface *intf, pm_message_t message)
{
	struct dfr_ctx *ctx = usb_get_intfdata(intf);
	int j;

	if (!ctx)
		return 0;               /* stub-claimed interface, nothing running */

	pr_info("dfr-probe: suspend - stopping the frame loop\n");
	ctx->stop = true;               /* frame_done() stops resubmitting */
	cancel_work_sync(&ctx->start_work);

	if (!usb_wait_anchor_empty_timeout(&ctx->tx_anchor, 1000))
		usb_kill_anchored_urbs(&ctx->tx_anchor);

	usb_kill_urb(ctx->in_urb);
	for (j = 0; j < 8; j++)
		if (ctx->in_pool[j])
			usb_kill_urb(ctx->in_pool[j]);
	return 0;
}

/* Resume. The panel loses its display session over suspend, so redo the whole
 * bring-up (clear halts -> GINF -> REDY -> CLDR -> first frame) rather than
 * just resuming traffic; dfr_start_work() is exactly that sequence.
 */
static int dfr_resume(struct usb_interface *intf)
{
	struct dfr_ctx *ctx = usb_get_intfdata(intf);

	if (!ctx)
		return 0;

	pr_info("dfr-probe: resume - restarting the handshake\n");
	ctx->stop = false;
	ctx->sent_ginf = false;
	ctx->drew = false;
	ctx->inited = false;
	ctx->got_info = false;
	ctx->frame = 0;
	schedule_work(&ctx->start_work);
	return 0;
}

static struct usb_driver dfr_driver = {
	.name = "dfr-probe", .id_table = dfr_ids,
	.probe = dfr_probe, .disconnect = dfr_disconnect,
	.suspend = dfr_suspend,
	.resume = dfr_resume,
	/* device lost power / was reset: same path, it needs the full handshake */
	.reset_resume = dfr_resume,
};

static int __init dfr_init(void)
{
	fb_buf = kmalloc(FB_MAX, GFP_KERNEL);
	if (!fb_buf)
		return -ENOMEM;
	user_fb = kzalloc(PANEL_FB_BYTES, GFP_KERNEL);
	if (!user_fb) {
		kfree(fb_buf);
		return -ENOMEM;
	}
	misc_register(&dfr_misc);
	pr_info("dfr-probe: /dev/dfr0 ready (%dx%d, %d bpp, %d bytes/frame)\n",
		PANEL_W, PANEL_H, PANEL_BPP, PANEL_FB_BYTES);
	return usb_register(&dfr_driver);
}
static void __exit dfr_exit(void)
{
	usb_deregister(&dfr_driver);
	misc_deregister(&dfr_misc);
	kfree(user_fb);
	kfree(fb_buf);
}
module_init(dfr_init);
module_exit(dfr_exit);

MODULE_DESCRIPTION("Apple T1 iBridge Touch Bar draw probe");
MODULE_LICENSE("GPL");
