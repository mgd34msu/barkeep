// SPDX-License-Identifier: GPL-2.0
/*
 * barkeep-cfgsel — select the USB configuration for the Apple T1 iBridge (05ac:8600)
 * at ENUMERATION time.
 *
 * Why this has to be a kernel module: on a composite USB device the configuration
 * can only be chosen by the enumerating driver. Nothing in userspace can change it
 * afterwards — SET_CONFIGURATION on an already-configured composite device is not
 * honoured (Microsoft documents the same rule for usbccgp). macOS does this with
 * kUSBPreferredConfiguration=2 on AppleUSBiBridge; Windows with the
 * OriginalConfigurationValue registry DWORD read by usbccgp. This is the Linux
 * equivalent, via usb_device_driver.choose_configuration.
 *
 * Config 1 (default): video + 2x HID, NO bulk OUT endpoint at all.
 * Config 2: adds interface 3, class 0x10, bulk OUT 0x02 / IN 0x85 = the DFR display.
 * Config 3: Apple's designated recovery configuration.
 *
 * WARNING: in config 2 the T1 stops drawing the function row itself and waits for the
 * host to supply a framebuffer. The Touch Bar will be BLANK until a userspace renderer
 * drives it. Set config=1 (or unload) and re-enumerate to get the function row back.
 */
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/usb.h>

#define IBRIDGE_VID 0x05ac
#define IBRIDGE_PID 0x8600

static int config = 1;   /* default: no behaviour change. Opt in with config=2. */
module_param(config, int, 0644);
MODULE_PARM_DESC(config,
	"bConfigurationValue to select for the T1 iBridge: "
	"1 = stock function row (default, safe), "
	"2 = DFR display config (Touch Bar goes blank until userspace renders), "
	"3 = Apple recovery config");

static const struct usb_device_id ibridge_ids[] = {
	{ USB_DEVICE(IBRIDGE_VID, IBRIDGE_PID) },
	{ }
};
MODULE_DEVICE_TABLE(usb, ibridge_ids);

static int ibridge_choose_configuration(struct usb_device *udev)
{
	int i;

	if (config < 1 || config > udev->descriptor.bNumConfigurations) {
		dev_warn(&udev->dev,
			 "barkeep-cfgsel: config=%d out of range (device has %d), leaving default\n",
			 config, udev->descriptor.bNumConfigurations);
		return -1;   /* fall back to usbcore's own choice */
	}

	/* verify the requested bConfigurationValue actually exists */
	for (i = 0; i < udev->descriptor.bNumConfigurations; i++) {
		if (udev->config[i].desc.bConfigurationValue == config) {
			dev_info(&udev->dev,
				 "barkeep-cfgsel: selecting configuration %d at enumeration\n",
				 config);
			return config;
		}
	}

	dev_warn(&udev->dev,
		 "barkeep-cfgsel: no configuration with bConfigurationValue=%d; leaving default\n",
		 config);
	return -1;
}

static struct usb_device_driver barkeep_cfgsel_driver = {
	.name			= "barkeep-cfgsel",
	.id_table		= ibridge_ids,
	.choose_configuration	= ibridge_choose_configuration,
	.generic_subclass	= 1,
	.supports_autosuspend	= 1,
};

static int __init barkeep_cfgsel_init(void)
{
	return usb_register_device_driver(&barkeep_cfgsel_driver, THIS_MODULE);
}

static void __exit barkeep_cfgsel_exit(void)
{
	usb_deregister_device_driver(&barkeep_cfgsel_driver);
}

module_init(barkeep_cfgsel_init);
module_exit(barkeep_cfgsel_exit);

MODULE_DESCRIPTION("Select USB configuration for the Apple T1 iBridge at enumeration");
MODULE_LICENSE("GPL");
