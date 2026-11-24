PORTNAME=	kilix-deps
DISTVERSION=	0.2.2
CATEGORIES=	x11

MAINTAINER=	itsmygithubacct@users.noreply.github.com
COMMENT=	Packages the Kilix terminal builds and runs against
WWW=		https://github.com/itsmygithubacct/kilix

LICENSE=	GPLv3

# Keep this list and freebsd_install() in scripts/install-build-deps.sh in step;
# tests/test_freebsd_packaging.py checks that every package there is here.
# Build: the toolchain, the fork engine's libraries, Pillow and Go.
RUN_DEPENDS=	bash>0:shells/bash \
		gmake>0:devel/gmake \
		pkgconf>0:devel/pkgconf \
		git>0:devel/git \
		curl>0:ftp/curl \
		go>0:lang/go \
		${PYTHON_PKGNAMEPREFIX}pillow>0:graphics/py-pillow@${PY_FLAVOR} \
		libX11>0:x11/libX11 \
		libXrandr>0:x11/libXrandr \
		libXinerama>0:x11/libXinerama \
		libXcursor>0:x11/libXcursor \
		libXi>0:x11/libXi \
		libxkbcommon>0:x11/libxkbcommon \
		mesa-libs>0:graphics/mesa-libs \
		dbus>0:devel/dbus \
		fontconfig>0:x11-fonts/fontconfig \
		freetype2>0:print/freetype2 \
		png>0:graphics/png \
		lcms2>0:graphics/lcms2 \
		cairo>0:graphics/cairo \
		harfbuzz>0:print/harfbuzz \
		xxhash>0:devel/xxhash \
		simde>0:devel/simde \
		wayland>0:graphics/wayland \
		wayland-protocols>0:graphics/wayland-protocols
# The desktop's Media Player (kilix-amp) and the text browser (kilix chawan).
RUN_DEPENDS+=	sdl2>0:devel/sdl20 \
		sdl2_image>0:graphics/sdl2_image \
		libsndfile>0:audio/libsndfile \
		fluidsynth>0:audio/fluidsynth \
		fluid-soundfont>0:audio/fluid-soundfont \
		libssh2>0:security/libssh2 \
		brotli>0:archivers/brotli
# The desktop's X11 apps: an Xvfb server per app and python-xlib.
RUN_DEPENDS+=	${PYTHON_PKGNAMEPREFIX}python-xlib>0:x11-toolkits/py-python-xlib@${PY_FLAVOR} \
		Xvfb:x11-servers/xorg-server@xvfb \
		xauth:x11/xauth

USES=		metaport python:run

.include <bsd.port.mk>
