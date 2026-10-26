// SPDX-License-Identifier: GPL-2.0
/*
 * haltia-vplay: Видео's video window (io.github.haltia_de.Video); other
 * programs may open it too (Файлы does).
 *
 *   haltia-vplay [--title TITLE] URL|FILE [mpv-option=value ...]
 *   haltia-vplay --queue [mpv-option=value ...]
 *
 * --queue starts with nothing to play and waits for a playlist over the
 * IPC (Видео's haltia-queue.lua, given as scripts=): the previous and next
 * buttons walk it, the title follows each entry, and the window closes
 * when the playlist ends (idle=once) instead of after the first video.
 *
 * libmpv decodes on the CPU and renders each frame at the video's own size
 * (a colour conversion, no scaling) into a wl_shm buffer on a subsurface;
 * the compositor scales it to the window on the GPU (wp_viewporter). mpv's
 * own wlshm output scaled every frame to the window in software and showed
 * 5-15 frames a second at 480p; this way 480p plays at its full 30.
 *
 * The controls are three strips over the video (the top bar, the buttons
 * across the middle, the bottom bar), not one window-sized layer: phoc on
 * the SGX draws a shm surface of 1000x600 or more opaque (alpha dropped,
 * see wlroots-0.20-hybris.patch), and a translucent layer that big hid
 * the whole video behind black. The strips are small enough to blend.
 *
 * A tap shows the controls for four seconds: the title, the middle buttons
 * that go back 10 s, pause or play, and go on 10 s (and, in a playlist,
 * the previous and next video further out), and the bar
 * at the bottom seeks (tap or drag). While the video plays, a logind
 * (systemd-logind or elogind) "idle" inhibitor keeps the screen on. The
 * window draws no close
 * button: a desktop's own closes it (the Haltia shell's «✕» in the corner).
 * Another window taking the screen pauses the video: hidden, the window
 * gets no frame callbacks, and the sound went on with no picture and no
 * way to stop it. The window closes at the end of the video. A YouTube or other page URL
 * goes through mpv's yt-dlp hook, at most 480p H.264 by default: more
 * than this CPU can convert in time.
 *
 * The main thread renders frames and handles Wayland, so it makes no call
 * that waits on mpv's core (mpv/render.h forbids it: the core may be
 * waiting for a frame, and on this CPU start, seek and a format change
 * stalled the picture): the video's size and state come as observed
 * properties, commands go async, and the logind call has a thread of its
 * own.
 */
#define _GNU_SOURCE
#include <cairo.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <pango/pangocairo.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/timerfd.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <gio/gio.h>
#include <gio/gunixfdlist.h>
#include <mpv/client.h>
#include <mpv/render.h>
#include <wayland-client.h>

#include "viewporter-client-protocol.h"
#include "xdg-shell-client-protocol.h"

#define UI_SHOW_MS 4000
#define BAR_H 120
#define TOP_H 112
#define SEEK_STEP 10
#define MID_H 200
/* The largest frame drawn: a file's own size, from any file, must not size
 * a buffer past what wl_shm (int32) and memory take. 4096 is beyond what
 * this CPU converts in time anyway. */
#define FRAME_MAX 4096

struct buf {
	struct wl_buffer *wb;
	void *data;
	size_t size;
	int w, h, stride;
	int busy;
};

static struct wl_display *dpy;
static struct wl_compositor *comp;
static struct wl_subcompositor *subcomp;
static struct wl_shm *shm;
static struct xdg_wm_base *wm;
static struct wp_viewporter *vporter;
static struct wl_seat *seat;

static struct wl_surface *base, *vsurf;
static struct wl_subsurface *vsub;
static struct wp_viewport *base_vp, *vid_vp;
static struct buf black, vbufs[2];

/* The controls: strips of the window, each drawn with the whole UI moved
 * up by its y, so that one drawing serves all three. */
enum { UI_TOP, UI_MID, UI_BOTTOM, UI_PARTS };
static struct uipart {
	struct wl_surface *s;
	struct wl_subsurface *sub;
	struct buf bufs[2];
	int y, h;
} ui[UI_PARTS];

static int win_w = 1160, win_h = 800, configured, frame_pending, want_frame, quit;
static int vx, vy, vw_shown, vh_shown, placed_w, placed_h, have_frame;
static int ui_visible = 1, ui_dirty = 1;
static int wake[2], ui_fd;
static mpv_handle *mpv;
static mpv_render_context *rc;

static struct xdg_toplevel *toplevel;
static char title[256];
static int queue_mode;
static int64_t pl_pos = -1, pl_count; /* where in the playlist, as mpv told it */
static char error_text[128];
static double t_pos = -1, t_dur = -1;
static int paused, last_sec = -1, was_active;
static int64_t dwidth, dheight; /* the video's size, as mpv last told it */

/* A finger on the controls. */
static int press_x, press_y, pressing, dragging;
static double drag_frac = -1;
static int32_t touch_id = -1;
static int ptr_x, ptr_y, ptr_down;

/* --- helpers -------------------------------------------------------------- */
/* A logind "idle" inhibitor: the screen stays on while its fd is open.
 * Without logind (or its bus) nothing is held, and the video plays all the
 * same. Only the hold thread calls this: the bus answers in its own time
 * (up to 3 s), and frames must not wait for it. */
static int inhibit_fd = -1;

static void logind_hold(int on)
{
	GDBusConnection *bus;
	GUnixFDList *fds = NULL;
	GVariant *res;
	GError *err = NULL;
	gint32 idx;

	if (!on) {
		if (inhibit_fd >= 0)
			close(inhibit_fd);
		inhibit_fd = -1;
		return;
	}
	if (inhibit_fd >= 0)
		return;
	bus = g_bus_get_sync(G_BUS_TYPE_SYSTEM, NULL, &err);
	if (!bus) {
		g_clear_error(&err);
		return;
	}
	res = g_dbus_connection_call_with_unix_fd_list_sync(
		bus, "org.freedesktop.login1", "/org/freedesktop/login1",
		"org.freedesktop.login1.Manager", "Inhibit",
		g_variant_new("(ssss)", "idle", "haltia-vplay", "a video plays", "block"),
		G_VARIANT_TYPE("(h)"), G_DBUS_CALL_FLAGS_NONE, 3000, NULL, &fds, NULL, &err);
	if (res) {
		g_variant_get(res, "(h)", &idx);
		inhibit_fd = g_unix_fd_list_get(fds, idx, NULL);
		g_variant_unref(res);
	}
	g_clear_error(&err);
	if (fds)
		g_object_unref(fds);
	g_object_unref(bus);
}

static GMutex hold_lock;
static GCond hold_cond;
static GThread *hold_thread;
static int hold_want, hold_done, hold_end;

/* Applies the last wish of hold_screen(), and lets go at the end. */
static gpointer hold_run(gpointer data)
{
	(void)data;
	g_mutex_lock(&hold_lock);
	for (;;) {
		int on;

		while (!hold_end && hold_want == hold_done)
			g_cond_wait(&hold_cond, &hold_lock);
		if (hold_end)
			break;
		on = hold_want;
		g_mutex_unlock(&hold_lock);
		logind_hold(on);
		g_mutex_lock(&hold_lock);
		hold_done = on;
	}
	g_mutex_unlock(&hold_lock);
	logind_hold(0);
	return NULL;
}

/* Hold the screen on while the video plays. */
static void hold_screen(int on)
{
	g_mutex_lock(&hold_lock);
	hold_want = on;
	g_cond_signal(&hold_cond);
	g_mutex_unlock(&hold_lock);
}

static void hold_finish(void)
{
	g_mutex_lock(&hold_lock);
	hold_end = 1;
	g_cond_signal(&hold_cond);
	g_mutex_unlock(&hold_lock);
	g_thread_join(hold_thread);
}

static void pause_video(void)
{
	int yes = 1;

	mpv_set_property_async(mpv, 0, "pause", MPV_FORMAT_FLAG, &yes);
}

static void fmt_time(double t, char *out, size_t n)
{
	int s = t > 0 ? (int)t : 0;

	if (s >= 3600)
		snprintf(out, n, "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60);
	else
		snprintf(out, n, "%d:%02d", s / 60, s % 60);
}

static void show_ui(int on)
{
	struct itimerspec t = { { 0, 0 }, { on ? UI_SHOW_MS / 1000 : 0,
					    on ? (UI_SHOW_MS % 1000) * 1000000L : 0 } };

	/* The controls stay while paused, loading or showing an error. */
	if (on && (paused || !have_frame || error_text[0]))
		t.it_value.tv_sec = t.it_value.tv_nsec = 0;
	timerfd_settime(ui_fd, 0, &t, NULL);
	if (ui_visible != on) {
		ui_visible = on;
		ui_dirty = 1;
	}
}

/* --- buffers -------------------------------------------------------------- */
static void buf_release(void *data, struct wl_buffer *wb)
{
	(void)wb;
	((struct buf *)data)->busy = 0;
}

static const struct wl_buffer_listener buf_listener = { buf_release };

static void buf_free(struct buf *b)
{
	if (b->wb)
		wl_buffer_destroy(b->wb);
	if (b->data)
		munmap(b->data, b->size);
	memset(b, 0, sizeof(*b));
}

static int buf_make(struct buf *b, int w, int h, uint32_t format)
{
	size_t stride, size;
	int fd;
	void *data;
	struct wl_shm_pool *pool;

	if (w <= 0 || h <= 0 || w > 16384 || h > 16384)
		return -1;
	stride = ((size_t)w * 4 + 63) & ~(size_t)63;
	size = stride * (size_t)h;
	if (size > INT32_MAX)
		return -1;
	fd = memfd_create("haltia-vplay", MFD_CLOEXEC);
	if (fd < 0)
		return -1;
	if (ftruncate(fd, (off_t)size) < 0) {
		close(fd);
		return -1;
	}
	data = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (data == MAP_FAILED) {
		close(fd);
		return -1;
	}
	pool = wl_shm_create_pool(shm, fd, (int32_t)size);
	b->wb = wl_shm_pool_create_buffer(pool, 0, w, h, (int32_t)stride, format);
	wl_shm_pool_destroy(pool);
	close(fd);
	wl_buffer_add_listener(b->wb, &buf_listener, b);
	b->data = data;
	b->size = size;
	b->w = w;
	b->h = h;
	b->stride = (int)stride;
	b->busy = 0;
	return 0;
}

static struct buf *free_buf(struct buf pair[2], int w, int h, uint32_t format)
{
	for (int i = 0; i < 2; i++) {
		if (pair[i].busy)
			continue;
		if (pair[i].wb && (pair[i].w != w || pair[i].h != h))
			buf_free(&pair[i]);
		if (!pair[i].wb && buf_make(&pair[i], w, h, format) < 0)
			continue;
		return &pair[i];
	}
	return NULL;
}

/* --- layout --------------------------------------------------------------- */
/* Fit the video into the window, keeping its shape, centred. */
static void place_video(int w, int h)
{
	double s = (double)win_w / w;

	if ((double)win_h / h < s)
		s = (double)win_h / h;
	vw_shown = (int)(w * s);
	vh_shown = (int)(h * s);
	vx = (win_w - vw_shown) / 2;
	vy = (win_h - vh_shown) / 2;
	wp_viewport_set_destination(vid_vp, vw_shown, vh_shown);
	wl_subsurface_set_position(vsub, vx, vy);
}

static void place_ui(void)
{
	ui[UI_TOP].y = 0;
	ui[UI_TOP].h = TOP_H;
	ui[UI_MID].h = MID_H;
	ui[UI_MID].y = (win_h - MID_H) / 2;
	ui[UI_BOTTOM].h = BAR_H;
	ui[UI_BOTTOM].y = win_h - BAR_H;
	for (int i = 0; i < UI_PARTS; i++)
		wl_subsurface_set_position(ui[i].sub, 0, ui[i].y); /* with the base */
}

static void commit_base(void)
{
	wp_viewport_set_destination(base_vp, win_w, win_h);
	wl_surface_attach(base, black.wb, 0, 0);
	wl_surface_damage_buffer(base, 0, 0, 1, 1);
	wl_surface_commit(base);
}

/* Control positions. */
#define BACK_X 16
#define BACK_Y 16
#define BACK_H 80
#define CORNER 128 /* the shell's close button, and a margin */
#define BTN_R 78
#define BTN_GAP 240

/* The middle row: the gap between button centres, closer on a narrow
 * window so that the previous and next buttons stay on it. */
static double btn_gap(void)
{
	double fit = (win_w / 2.0 - BTN_R * 0.8 - 16) / 2;

	return pl_count > 1 && fit < BTN_GAP ? fit : BTN_GAP;
}

static int pl_can(int dir)
{
	return dir < 0 ? pl_pos > 0 : pl_pos >= 0 && pl_pos < pl_count - 1;
}

static void bar_span(int *x0, int *x1)
{
	*x0 = 150;
	*x1 = win_w - 150;
}

/* --- drawing the controls -------------------------------------------------- */
static void rounded(cairo_t *cr, double x, double y, double w, double h, double r)
{
	cairo_new_sub_path(cr);
	cairo_arc(cr, x + w - r, y + r, r, -M_PI / 2, 0);
	cairo_arc(cr, x + w - r, y + h - r, r, 0, M_PI / 2);
	cairo_arc(cr, x + r, y + h - r, r, M_PI / 2, M_PI);
	cairo_arc(cr, x + r, y + r, r, M_PI, 3 * M_PI / 2);
	cairo_close_path(cr);
}

static void text(cairo_t *cr, PangoLayout *pl, const char *font, const char *s, double x,
		 double y, int align, int max_w)
{
	PangoFontDescription *fd = pango_font_description_from_string(font);
	int tw, th;

	pango_layout_set_font_description(pl, fd);
	pango_font_description_free(fd);
	pango_layout_set_width(pl, max_w > 0 ? max_w * PANGO_SCALE : -1);
	pango_layout_set_ellipsize(pl, max_w > 0 ? PANGO_ELLIPSIZE_END : PANGO_ELLIPSIZE_NONE);
	pango_layout_set_text(pl, s, -1);
	pango_layout_get_pixel_size(pl, &tw, &th);
	if (align == 0)
		x -= tw / 2.0;
	else if (align > 0)
		x -= tw;
	cairo_move_to(cr, x, y - th / 2.0);
	pango_cairo_show_layout(cr, pl);
}

static void draw_controls(cairo_t *cr, PangoLayout *pl)
{
	double cx = win_w / 2.0, cy = win_h / 2.0, frac;
	char a[32], d[32];
	int x0, x1;

	/* Top: the title. The window is closed by the shell's "✕" in the top
	 * right corner (b8k-close), as every program is; the title stops
	 * short of it. */
	cairo_set_source_rgba(cr, 0, 0, 0, 0.6);
	cairo_rectangle(cr, 0, 0, win_w, TOP_H);
	cairo_fill(cr);
	cairo_set_source_rgb(cr, 1, 1, 1);
	text(cr, pl, "DejaVu Sans 24", title, BACK_X + 8, BACK_Y + BACK_H / 2.0, -1,
	     win_w - BACK_X - 8 - CORNER);

	if (error_text[0] || !have_frame) {
		cairo_set_source_rgb(cr, 1, 1, 1);
		text(cr, pl, "DejaVu Sans 30", error_text[0] ? error_text : "Загрузка…", cx, cy,
		     0, win_w - 80);
	} else {
		/* Middle: back 10 s, pause or play, on 10 s; in a playlist the
		 * previous and next video further out, dim at either end. */
		int n = pl_count > 1 ? 2 : 1;

		for (int i = -n; i <= n; i++) {
			double bx = cx + i * btn_gap(), r = i ? BTN_R * 0.8 : BTN_R;

			cairo_arc(cr, bx, cy, r, 0, 2 * M_PI);
			cairo_set_source_rgba(cr, 0, 0, 0, 0.55);
			cairo_fill(cr);
			cairo_set_source_rgba(cr, 1, 1, 1, i * i < 4 || pl_can(i) ? 1 : 0.35);
			if (i * i == 4) {
				double s = i < 0 ? -1 : 1; /* the triangles point this way */

				cairo_move_to(cr, bx - s * 24, cy - 28);
				cairo_line_to(cr, bx + s * 14, cy);
				cairo_line_to(cr, bx - s * 24, cy + 28);
				cairo_close_path(cr);
				cairo_rectangle(cr, s > 0 ? bx + 16 : bx - 24, cy - 28, 8, 56);
				cairo_fill(cr);
			} else if (i) {
				text(cr, pl, "DejaVu Sans Bold 26", i < 0 ? "−10" : "+10", bx, cy, 0, 0);
			} else if (paused) {
				cairo_move_to(cr, bx - 22, cy - 34);
				cairo_line_to(cr, bx + 34, cy);
				cairo_line_to(cr, bx - 22, cy + 34);
				cairo_close_path(cr);
				cairo_fill(cr);
			} else {
				cairo_rectangle(cr, bx - 26, cy - 32, 18, 64);
				cairo_rectangle(cr, bx + 8, cy - 32, 18, 64);
				cairo_fill(cr);
			}
		}
	}

	/* Bottom: the time and the bar. */
	cairo_set_source_rgba(cr, 0, 0, 0, 0.6);
	cairo_rectangle(cr, 0, win_h - BAR_H, win_w, BAR_H);
	cairo_fill(cr);
	bar_span(&x0, &x1);
	frac = drag_frac >= 0 ? drag_frac : (t_dur > 0 && t_pos >= 0 ? t_pos / t_dur : 0);
	if (frac > 1)
		frac = 1;
	fmt_time(drag_frac >= 0 ? drag_frac * t_dur : t_pos, a, sizeof(a));
	fmt_time(t_dur, d, sizeof(d));
	cairo_set_source_rgb(cr, 1, 1, 1);
	text(cr, pl, "DejaVu Sans 22", a, x0 - 24, win_h - BAR_H / 2.0, 1, 0);
	text(cr, pl, "DejaVu Sans 22", t_dur > 0 ? d : "", x1 + 24, win_h - BAR_H / 2.0, -1, 0);
	rounded(cr, x0, win_h - BAR_H / 2.0 - 6, x1 - x0, 12, 6);
	cairo_set_source_rgba(cr, 1, 1, 1, 0.3);
	cairo_fill(cr);
	if (t_dur > 0) {
		rounded(cr, x0, win_h - BAR_H / 2.0 - 6, (x1 - x0) * frac + 1, 12, 6);
		cairo_set_source_rgb(cr, 0x35 / 255.0, 0x84 / 255.0, 0xe4 / 255.0);
		cairo_fill(cr);
		cairo_arc(cr, x0 + (x1 - x0) * frac, win_h - BAR_H / 2.0, 18, 0, 2 * M_PI);
		cairo_set_source_rgb(cr, 1, 1, 1);
		cairo_fill(cr);
	}

}

static void draw_ui(void)
{
	struct buf *b[UI_PARTS];

	ui_dirty = 0;
	if (!ui_visible) {
		for (int i = 0; i < UI_PARTS; i++) {
			wl_surface_attach(ui[i].s, NULL, 0, 0);
			wl_surface_commit(ui[i].s);
		}
		return;
	}
	for (int i = 0; i < UI_PARTS; i++) {
		b[i] = free_buf(ui[i].bufs, win_w, ui[i].h, WL_SHM_FORMAT_ARGB8888);
		if (!b[i]) {
			ui_dirty = 1; /* both held: try again on the next event */
			return;
		}
	}
	for (int i = 0; i < UI_PARTS; i++) {
		cairo_surface_t *s = cairo_image_surface_create_for_data(
			b[i]->data, CAIRO_FORMAT_ARGB32, b[i]->w, b[i]->h, b[i]->stride);
		cairo_t *cr = cairo_create(s);
		PangoLayout *pl = pango_cairo_create_layout(cr);

		cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
		cairo_set_source_rgba(cr, 0, 0, 0, 0);
		cairo_paint(cr);
		cairo_set_operator(cr, CAIRO_OPERATOR_OVER);
		cairo_translate(cr, 0, -ui[i].y);
		draw_controls(cr, pl);
		g_object_unref(pl);
		cairo_destroy(cr);
		cairo_surface_flush(s);
		cairo_surface_destroy(s);
		wl_surface_attach(ui[i].s, b[i]->wb, 0, 0);
		wl_surface_damage_buffer(ui[i].s, 0, 0, b[i]->w, b[i]->h);
		b[i]->busy = 1;
		wl_surface_commit(ui[i].s);
	}
}

/* --- video frames ---------------------------------------------------------- */
static void frame_done(void *data, struct wl_callback *cb, uint32_t t)
{
	(void)data;
	(void)t;
	wl_callback_destroy(cb);
	frame_pending = 0;
}

static const struct wl_callback_listener frame_listener = { frame_done };

static void draw_video(void)
{
	int64_t w = dwidth, h = dheight;
	struct buf *b;
	int size[2];
	size_t stride;

	if (w <= 0 || h <= 0)
		return;
	if (w > FRAME_MAX || h > FRAME_MAX) {
		if (!error_text[0]) {
			snprintf(error_text, sizeof(error_text),
				 "Видео слишком большое: %lldx%lld", (long long)w, (long long)h);
			pause_video(); /* no sound without picture */
			show_ui(1);
			ui_dirty = 1;
		}
		want_frame = 0;
		return;
	}
	b = free_buf(vbufs, (int)w, (int)h, WL_SHM_FORMAT_XRGB8888);
	if (!b)
		return;
	size[0] = b->w;
	size[1] = b->h;
	stride = (size_t)b->stride;
	mpv_render_param p[] = {
		{ MPV_RENDER_PARAM_SW_SIZE, size },
		{ MPV_RENDER_PARAM_SW_FORMAT, "bgr0" },
		{ MPV_RENDER_PARAM_SW_STRIDE, &stride },
		{ MPV_RENDER_PARAM_SW_POINTER, b->data },
		{ 0 },
	};
	mpv_render_context_render(rc, p);
	want_frame = 0;
	if (b->w != placed_w || b->h != placed_h) {
		placed_w = b->w;
		placed_h = b->h;
		place_video(b->w, b->h);
		commit_base(); /* the subsurface position applies with it */
	}
	if (!have_frame) {
		have_frame = 1;
		show_ui(1); /* the controls now go away by themselves */
		ui_dirty = 1;
	}
	wl_surface_attach(vsurf, b->wb, 0, 0);
	wl_surface_damage_buffer(vsurf, 0, 0, b->w, b->h);
	wl_callback_add_listener(wl_surface_frame(vsurf), &frame_listener, NULL);
	frame_pending = 1;
	b->busy = 1;
	wl_surface_commit(vsurf);
	mpv_render_context_report_swap(rc);
}

/* --- taps ------------------------------------------------------------------ */
static void seek_rel(double s)
{
	char arg[32];
	const char *cmd[] = { "seek", arg, "relative", NULL };

	snprintf(arg, sizeof(arg), "%g", s);
	mpv_command_async(mpv, 0, cmd);
}

static void seek_frac(double f)
{
	char arg[32];
	const char *cmd[] = { "seek", arg, "absolute-percent", NULL };

	snprintf(arg, sizeof(arg), "%g", f * 100);
	mpv_command_async(mpv, 0, cmd);
}

static int on_bar(int x, int y)
{
	int x0, x1;

	bar_span(&x0, &x1);
	return y > win_h - BAR_H && x >= x0 - 30 && x <= x1 + 30 && t_dur > 0;
}

static double bar_frac(int x)
{
	int x0, x1;
	double f;

	bar_span(&x0, &x1);
	f = (double)(x - x0) / (x1 - x0);
	return f < 0 ? 0 : (f > 1 ? 1 : f);
}

static void press_down(int x, int y)
{
	pressing = 1;
	press_x = x;
	press_y = y;
	dragging = ui_visible && on_bar(x, y);
	if (dragging) {
		drag_frac = bar_frac(x);
		show_ui(1);
		ui_dirty = 1;
	}
}

static void press_move(int x, int y)
{
	(void)y;
	if (pressing && dragging) {
		drag_frac = bar_frac(x);
		ui_dirty = 1;
	}
}

static void press_up(void)
{
	int x = press_x, y = press_y;

	if (!pressing)
		return;
	pressing = 0;
	if (dragging) {
		dragging = 0;
		seek_frac(drag_frac);
		show_ui(1);
		return; /* drag_frac stays until mpv reports the new position */
	}
	if (!ui_visible) {
		show_ui(1);
		return;
	}
	if (have_frame && !error_text[0]) {
		double cx = win_w / 2.0, cy = win_h / 2.0;

		int n = pl_count > 1 ? 2 : 1;

		for (int i = -n; i <= n; i++) {
			double dx = x - (cx + i * btn_gap()), dy = y - cy;

			if (dx * dx + dy * dy > BTN_R * BTN_R * 1.3)
				continue;
			if (i * i == 4) {
				if (pl_can(i))
					mpv_command_async(mpv, 0, (const char *[]){
						i < 0 ? "playlist-prev" : "playlist-next", NULL });
			} else if (i)
				seek_rel(i * SEEK_STEP);
			else
				mpv_command_async(mpv, 0, (const char *[]){ "cycle", "pause", NULL });
			show_ui(1);
			return;
		}
	}
	if (y < TOP_H || y > win_h - BAR_H)
		return; /* on a bar but not on a control */
	show_ui(0);
}

static void touch_down(void *d, struct wl_touch *t, uint32_t serial, uint32_t time,
		       struct wl_surface *s, int32_t id, wl_fixed_t x, wl_fixed_t y)
{
	(void)d; (void)t; (void)serial; (void)time;
	if (touch_id >= 0)
		return;
	touch_id = id;
	(void)s; /* the video takes no input: all in window terms */
	press_down(wl_fixed_to_int(x), wl_fixed_to_int(y));
}

static void touch_up(void *d, struct wl_touch *t, uint32_t serial, uint32_t time, int32_t id)
{
	(void)d; (void)t; (void)serial; (void)time;
	if (id != touch_id)
		return;
	touch_id = -1;
	press_up();
}

static void touch_motion(void *d, struct wl_touch *t, uint32_t time, int32_t id, wl_fixed_t x,
			 wl_fixed_t y)
{
	(void)d; (void)t; (void)time;
	if (id == touch_id)
		press_move(wl_fixed_to_int(x), wl_fixed_to_int(y));
}

static void touch_frame(void *d, struct wl_touch *t)
{
	(void)d; (void)t;
}

static void touch_cancel(void *d, struct wl_touch *t)
{
	(void)d; (void)t;
	touch_id = -1;
	pressing = dragging = 0;
	drag_frac = -1;
}

static void touch_shape(void *d, struct wl_touch *t, int32_t id, wl_fixed_t a, wl_fixed_t b)
{
	(void)d; (void)t; (void)id; (void)a; (void)b;
}

static void touch_orientation(void *d, struct wl_touch *t, int32_t id, wl_fixed_t o)
{
	(void)d; (void)t; (void)id; (void)o;
}

static const struct wl_touch_listener touch_listener = {
	touch_down, touch_up, touch_motion, touch_frame, touch_cancel, touch_shape,
	touch_orientation,
};

static void ptr_enter(void *d, struct wl_pointer *p, uint32_t serial, struct wl_surface *s,
		      wl_fixed_t x, wl_fixed_t y)
{
	(void)d; (void)p; (void)serial; (void)s;
	ptr_x = wl_fixed_to_int(x);
	ptr_y = wl_fixed_to_int(y);
}

static void ptr_leave(void *d, struct wl_pointer *p, uint32_t serial, struct wl_surface *s)
{
	(void)d; (void)p; (void)serial; (void)s;
}

static void ptr_motion(void *d, struct wl_pointer *p, uint32_t time, wl_fixed_t x, wl_fixed_t y)
{
	(void)d; (void)p; (void)time;
	ptr_x = wl_fixed_to_int(x);
	ptr_y = wl_fixed_to_int(y);
	if (ptr_down)
		press_move(ptr_x, ptr_y);
	else if (!ui_visible)
		show_ui(1);
}

static void ptr_button(void *d, struct wl_pointer *p, uint32_t serial, uint32_t time,
		       uint32_t button, uint32_t state)
{
	(void)d; (void)p; (void)serial; (void)time;
	if (button != 0x110)
		return;
	if (state == WL_POINTER_BUTTON_STATE_PRESSED) {
		ptr_down = 1;
		press_down(ptr_x, ptr_y);
	} else if (ptr_down) {
		ptr_down = 0;
		press_up();
	}
}

static void ptr_axis(void *d, struct wl_pointer *p, uint32_t time, uint32_t axis, wl_fixed_t v)
{
	(void)d; (void)p; (void)time; (void)axis; (void)v;
}

static const struct wl_pointer_listener pointer_listener = {
	.enter = ptr_enter,
	.leave = ptr_leave,
	.motion = ptr_motion,
	.button = ptr_button,
	.axis = ptr_axis,
};

static void seat_caps(void *d, struct wl_seat *s, uint32_t caps)
{
	static struct wl_touch *touch;
	static struct wl_pointer *pointer;

	(void)d;
	if ((caps & WL_SEAT_CAPABILITY_TOUCH) && !touch) {
		touch = wl_seat_get_touch(s);
		wl_touch_add_listener(touch, &touch_listener, NULL);
	}
	if ((caps & WL_SEAT_CAPABILITY_POINTER) && !pointer) {
		pointer = wl_seat_get_pointer(s);
		wl_pointer_add_listener(pointer, &pointer_listener, NULL);
	}
}

static void seat_name(void *d, struct wl_seat *s, const char *name)
{
	(void)d; (void)s; (void)name;
}

static const struct wl_seat_listener seat_listener = { seat_caps, seat_name };

/* --- the window ------------------------------------------------------------ */
static void wm_ping(void *data, struct xdg_wm_base *w, uint32_t serial)
{
	(void)data;
	xdg_wm_base_pong(w, serial);
}

static const struct xdg_wm_base_listener wm_listener = { wm_ping };

static void xs_configure(void *data, struct xdg_surface *xs, uint32_t serial)
{
	(void)data;
	xdg_surface_ack_configure(xs, serial);
	configured = 1;
	if (placed_w)
		place_video(placed_w, placed_h);
	place_ui();
	commit_base();
	ui_dirty = 1;
	want_frame = 1;
}

static const struct xdg_surface_listener xs_listener = { xs_configure };

static void tl_configure(void *data, struct xdg_toplevel *t, int32_t w, int32_t h,
			 struct wl_array *states)
{
	uint32_t *state;
	int active = 0;

	(void)data; (void)t;
	if (w > 0 && h > 0) {
		win_w = w;
		win_h = h;
	}
	wl_array_for_each(state, states)
		if (*state == XDG_TOPLEVEL_STATE_ACTIVATED)
			active = 1;
	if (was_active && !active && mpv && !paused)
		pause_video(); /* back: tap play */
	was_active = active;
}

static void tl_close(void *data, struct xdg_toplevel *t)
{
	(void)data; (void)t;
	quit = 1;
}

static void tl_bounds(void *data, struct xdg_toplevel *t, int32_t w, int32_t h)
{
	(void)data; (void)t; (void)w; (void)h;
}

static void tl_caps(void *data, struct xdg_toplevel *t, struct wl_array *c)
{
	(void)data; (void)t; (void)c;
}

static const struct xdg_toplevel_listener tl_listener = {
	tl_configure, tl_close, tl_bounds, tl_caps,
};

static void reg_global(void *data, struct wl_registry *r, uint32_t name, const char *iface,
		       uint32_t ver)
{
	(void)data;
	if (!strcmp(iface, wl_compositor_interface.name))
		comp = wl_registry_bind(r, name, &wl_compositor_interface, 4);
	else if (!strcmp(iface, wl_subcompositor_interface.name))
		subcomp = wl_registry_bind(r, name, &wl_subcompositor_interface, 1);
	else if (!strcmp(iface, wl_shm_interface.name))
		shm = wl_registry_bind(r, name, &wl_shm_interface, 1);
	else if (!strcmp(iface, xdg_wm_base_interface.name))
		wm = wl_registry_bind(r, name, &xdg_wm_base_interface, 1);
	else if (!strcmp(iface, wp_viewporter_interface.name))
		vporter = wl_registry_bind(r, name, &wp_viewporter_interface, 1);
	else if (!strcmp(iface, wl_seat_interface.name) && !seat) {
		seat = wl_registry_bind(r, name, &wl_seat_interface, ver < 5 ? ver : 5);
		wl_seat_add_listener(seat, &seat_listener, NULL);
	}
}

static void reg_remove(void *data, struct wl_registry *r, uint32_t name)
{
	(void)data; (void)r; (void)name;
}

static const struct wl_registry_listener reg_listener = { reg_global, reg_remove };

static void poke(void *ctx)
{
	char c = 1;

	(void)ctx;
	if (write(wake[1], &c, 1) < 0 && errno != EAGAIN)
		perror("haltia-vplay: wake");
}

/* --- mpv events ------------------------------------------------------------ */
static void mpv_events(void)
{
	for (;;) {
		mpv_event *ev = mpv_wait_event(mpv, 0);

		if (ev->event_id == MPV_EVENT_NONE)
			break;
		if (ev->event_id == MPV_EVENT_SHUTDOWN) {
			quit = 1;
		} else if (ev->event_id == MPV_EVENT_START_FILE) {
			/* The next in a playlist: loading again, from its start. */
			error_text[0] = '\0';
			have_frame = 0;
			t_pos = t_dur = drag_frac = -1;
			last_sec = -1;
			show_ui(1);
			ui_dirty = 1;
		} else if (ev->event_id == MPV_EVENT_END_FILE) {
			mpv_event_end_file *ef = ev->data;

			if (ef->reason == MPV_END_FILE_REASON_ERROR) {
				snprintf(error_text, sizeof(error_text),
					 "Не удалось открыть видео: %s", mpv_error_string(ef->error));
				show_ui(1);
				ui_dirty = 1;
				hold_screen(0);
			} else if (!queue_mode) {
				quit = 1; /* a queue ends with idle=once's shutdown */
			}
		} else if (ev->event_id == MPV_EVENT_PROPERTY_CHANGE) {
			mpv_event_property *p = ev->data;

			if (!strcmp(p->name, "time-pos")) {
				t_pos = p->format == MPV_FORMAT_DOUBLE ? *(double *)p->data : -1;
				if ((int)t_pos != last_sec) {
					last_sec = (int)t_pos;
					if (ui_visible)
						ui_dirty = 1;
				}
				if (drag_frac >= 0 && !pressing)
					drag_frac = -1;
			} else if (!strcmp(p->name, "dwidth") || !strcmp(p->name, "dheight")) {
				int64_t v = p->format == MPV_FORMAT_INT64 ? *(int64_t *)p->data : 0;

				*(p->name[1] == 'w' ? &dwidth : &dheight) = v;
			} else if (!strcmp(p->name, "duration")) {
				t_dur = p->format == MPV_FORMAT_DOUBLE ? *(double *)p->data : -1;
				ui_dirty = 1;
			} else if (!strcmp(p->name, "pause") && p->format == MPV_FORMAT_FLAG) {
				paused = *(int *)p->data;
				hold_screen(!paused);
				show_ui(ui_visible || paused);
				ui_dirty = 1;
			} else if (!strcmp(p->name, "media-title") && p->format == MPV_FORMAT_STRING &&
				   (queue_mode || !title[0])) {
				snprintf(title, sizeof(title), "%s", *(char **)p->data);
				xdg_toplevel_set_title(toplevel, title);
				ui_dirty = 1;
			} else if (!strcmp(p->name, "playlist-pos") || !strcmp(p->name, "playlist-count")) {
				int64_t v = p->format == MPV_FORMAT_INT64 ? *(int64_t *)p->data : -1;

				*(p->name[9] == 'p' ? &pl_pos : &pl_count) = v;
				ui_dirty = 1;
			}
		}
	}
}

int main(int argc, char **argv)
{
	const char *url = NULL;
	struct xdg_surface *xs;
	struct xdg_toplevel *tl;
	struct wl_region *empty;
	int i = 1;

	if (argc > 2 && !strcmp(argv[1], "--title")) {
		snprintf(title, sizeof(title), "%s", argv[2]);
		i = 3;
	}
	if (i < argc && !strcmp(argv[i], "--queue")) {
		queue_mode = 1;
		i++;
	} else if (i >= argc) {
		fprintf(stderr, "usage: %s [--title TITLE] URL|FILE [option=value ...]\n"
			"       %s --queue [option=value ...]\n", argv[0], argv[0]);
		return 2;
	} else {
		url = argv[i++];
	}
	if (pipe2(wake, O_CLOEXEC | O_NONBLOCK) < 0)
		return 1;
	ui_fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);

	dpy = wl_display_connect(NULL);
	if (!dpy) {
		fprintf(stderr, "haltia-vplay: no Wayland display\n");
		return 1;
	}
	wl_registry_add_listener(wl_display_get_registry(dpy), &reg_listener, NULL);
	wl_display_roundtrip(dpy);
	if (!comp || !subcomp || !shm || !wm || !vporter) {
		fprintf(stderr, "haltia-vplay: the compositor lacks a needed interface\n");
		return 1;
	}
	xdg_wm_base_add_listener(wm, &wm_listener, NULL);

	/* A black 1x1 buffer stretched over the window behind the video. */
	if (buf_make(&black, 1, 1, WL_SHM_FORMAT_XRGB8888) < 0)
		return 1;
	*(uint32_t *)black.data = 0;
	base = wl_compositor_create_surface(comp);
	base_vp = wp_viewporter_get_viewport(vporter, base);
	vsurf = wl_compositor_create_surface(comp);
	vid_vp = wp_viewporter_get_viewport(vporter, vsurf);
	vsub = wl_subcompositor_get_subsurface(subcomp, vsurf, base);
	wl_subsurface_set_desync(vsub);
	/* Taps on the video and the controls go to the window underneath, in
	 * window terms. */
	empty = wl_compositor_create_region(comp);
	wl_surface_set_input_region(vsurf, empty);
	for (int i = 0; i < UI_PARTS; i++) {
		ui[i].s = wl_compositor_create_surface(comp);
		ui[i].sub = wl_subcompositor_get_subsurface(subcomp, ui[i].s, base);
		wl_subsurface_set_desync(ui[i].sub);
		wl_subsurface_place_above(ui[i].sub, vsurf);
		wl_surface_set_input_region(ui[i].s, empty);
	}
	wl_region_destroy(empty);

	xs = xdg_wm_base_get_xdg_surface(wm, base);
	xdg_surface_add_listener(xs, &xs_listener, NULL);
	tl = toplevel = xdg_surface_get_toplevel(xs);
	xdg_toplevel_add_listener(tl, &tl_listener, NULL);
	xdg_toplevel_set_app_id(tl, "haltia-vplay");
	xdg_toplevel_set_title(tl, title[0] ? title : "Видео");
	wl_surface_commit(base);

	mpv = mpv_create();
	if (!mpv)
		return 1;
	mpv_set_option_string(mpv, "vo", "libmpv");
	mpv_set_option_string(mpv, "hwdec", "no");
	mpv_set_option_string(mpv, "terminal", "yes");
	mpv_set_option_string(mpv, "msg-level", "all=warn");
	mpv_set_option_string(mpv, "input-default-bindings", "no");
	mpv_set_option_string(mpv, "demuxer-max-bytes", "48MiB");
	mpv_set_option_string(mpv, "demuxer-max-back-bytes", "16MiB");
	/* H.264 at most 480p (VP9 and AV1 cost more), and the AAC audio:
	 * YouTube answered 403 to the Opus stream. */
	mpv_set_option_string(mpv, "ytdl-format",
			      "bv*[height<=480][vcodec^=avc1]+ba[ext=m4a]/b[height<=480]/b");
	mpv_set_option_string(mpv, "ytdl-raw-options", "js-runtimes=quickjs");
	if (title[0])
		mpv_set_option_string(mpv, "force-media-title", title);
	if (queue_mode)
		mpv_set_option_string(mpv, "idle", "once");
	for (; i < argc; i++) {
		char *eq = strchr(argv[i], '=');

		if (!eq)
			continue;
		*eq = '\0';
		if (mpv_set_option_string(mpv, argv[i], eq + 1) < 0)
			fprintf(stderr, "haltia-vplay: bad option %s\n", argv[i]);
	}
	if (mpv_initialize(mpv) < 0)
		return 1;
	mpv_render_param rp[] = {
		{ MPV_RENDER_PARAM_API_TYPE, MPV_RENDER_API_TYPE_SW },
		{ 0 },
	};
	if (mpv_render_context_create(&rc, mpv, rp) < 0) {
		fprintf(stderr, "haltia-vplay: no software render context\n");
		return 1;
	}
	mpv_observe_property(mpv, 0, "time-pos", MPV_FORMAT_DOUBLE);
	mpv_observe_property(mpv, 0, "duration", MPV_FORMAT_DOUBLE);
	mpv_observe_property(mpv, 0, "pause", MPV_FORMAT_FLAG);
	mpv_observe_property(mpv, 0, "media-title", MPV_FORMAT_STRING);
	mpv_observe_property(mpv, 0, "dwidth", MPV_FORMAT_INT64);
	mpv_observe_property(mpv, 0, "dheight", MPV_FORMAT_INT64);
	mpv_observe_property(mpv, 0, "playlist-pos", MPV_FORMAT_INT64);
	mpv_observe_property(mpv, 0, "playlist-count", MPV_FORMAT_INT64);
	mpv_set_wakeup_callback(mpv, poke, NULL);
	mpv_render_context_set_update_callback(rc, poke, NULL);
	if (url) {
		const char *cmd[] = { "loadfile", url, NULL };

		mpv_command_async(mpv, 0, cmd);
	}
	hold_thread = g_thread_new("hold", hold_run, NULL);
	hold_screen(1);

	while (!quit) {
		struct pollfd pf[3] = {
			{ wl_display_get_fd(dpy), POLLIN, 0 },
			{ wake[0], POLLIN, 0 },
			{ ui_fd, POLLIN, 0 },
		};

		if (configured && ui_dirty)
			draw_ui();
		while (wl_display_prepare_read(dpy) != 0)
			wl_display_dispatch_pending(dpy);
		wl_display_flush(dpy);
		if (poll(pf, 3, -1) < 0 && errno != EINTR) {
			wl_display_cancel_read(dpy);
			break;
		}
		if (pf[0].revents & POLLIN) {
			if (wl_display_read_events(dpy) < 0)
				break;
		} else {
			wl_display_cancel_read(dpy);
		}
		if (wl_display_dispatch_pending(dpy) < 0)
			break;
		if (pf[1].revents & POLLIN) {
			char c[64];

			while (read(wake[0], c, sizeof(c)) > 0)
				;
			mpv_events();
			if (mpv_render_context_update(rc) & MPV_RENDER_UPDATE_FRAME)
				want_frame = 1;
		}
		if (pf[2].revents & POLLIN) {
			uint64_t n;

			if (read(ui_fd, &n, sizeof(n)) < 0)
				n = 0;
			if (!paused && have_frame && !error_text[0] && !pressing)
				show_ui(0);
		}
		if (configured && want_frame && !frame_pending)
			draw_video();
	}
	mpv_render_context_free(rc);
	mpv_terminate_destroy(mpv);
	wl_display_disconnect(dpy);
	hold_finish(); /* lets go of the screen, the window gone meanwhile */
	return 0;
}
