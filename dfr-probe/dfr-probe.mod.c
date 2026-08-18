#include <linux/module.h>
#include <linux/export-internal.h>
#include <linux/compiler.h>

MODULE_INFO(name, KBUILD_MODNAME);

__visible struct module __this_module
__section(".gnu.linkonce.this_module") = {
	.name = KBUILD_MODNAME,
	.init = init_module,
#ifdef CONFIG_MODULE_UNLOAD
	.exit = cleanup_module,
#endif
	.arch = MODULE_ARCH_INIT,
};


MODULE_INFO(depends, "");

MODULE_ALIAS("usb:v05ACp8600d*dc*dsc*dp*ic10isc00ip00in*");
MODULE_ALIAS("usb:v05ACp8600d*dc*dsc*dp*ic02isc0Dip00in*");
MODULE_ALIAS("usb:v05ACp8600d*dc*dsc*dp*ic0Aisc00ip01in*");
MODULE_ALIAS("usb:v05ACp8600d*dc*dsc*dp*ic03isc00ip01in*");
MODULE_ALIAS("usb:v05ACp8600d*dc*dsc*dp*ic03isc01ip01in*");
MODULE_ALIAS("usb:v05ACp8600d*dc*dsc*dp*ic0Eisc01ip00in*");
MODULE_ALIAS("usb:v05ACp8600d*dc*dsc*dp*ic0Eisc02ip00in*");

MODULE_INFO(srcversion, "D80580BE051750113053B20");
