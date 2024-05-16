"""vSphere cloud adaptor"""

from sky.adaptors import common

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for vSphere. '
                         'Try running: pip install "skypilot[vsphere]"')
vmware_vapi = common.LazyImport('vmware.vapi',
                                import_error_message=_IMPORT_ERROR_MESSAGE)
com_vmware = common.LazyImport('com.vmware',
                               import_error_message=_IMPORT_ERROR_MESSAGE)
pyVim = common.LazyImport('pyVim', import_error_message=_IMPORT_ERROR_MESSAGE)
pyVmomi = common.LazyImport('pyVmomi',
                            import_error_message=_IMPORT_ERROR_MESSAGE)


def get_tagging_client():
    return com_vmware.cis.tagging_client


def get_vapi_connect():
    return vmware_vapi.lib.connect


def get_security_session():
    return vmware_vapi.security


def get_user_password():
    return vmware_vapi.security


def get_factories():
    return vmware_vapi.stdlib.client.factories


def get_cis_client():
    return com_vmware.cis_client


def get_pyvim_connect():
    return pyVim.connect


def get_std_client():
    return com_vmware.vapi


def get_downloadsession_client():
    return com_vmware.content.library.item.downloadsession_client


def get_updatesession_client():
    return com_vmware.content.library.item.updatesession_client


def get_item_client():
    return com_vmware.content.library.item_client


def get_content_client():
    return com_vmware.content_client


def get_iso_client():
    return com_vmware.vcenter.iso_client


def get_ovf_client():
    return com_vmware.vcenter.ovf_client


def get_library_items_client():
    return com_vmware.vcenter.vm_template.library_items_client


def get_vm_template_client():
    return com_vmware.vcenter.vm_template_client


def get_vcenter_client():
    return com_vmware.vcenter_client


def get_library_client():
    return com_vmware.content.library_client


def get_vim():
    return pyVmomi.vim


def get_vmodl():
    return pyVmomi.vmodl


def get_pyvmomi():
    return pyVmomi


def get_pbm():
    return pyVmomi.pbm


def get_vmomi_support():
    return pyVmomi.VmomiSupport
