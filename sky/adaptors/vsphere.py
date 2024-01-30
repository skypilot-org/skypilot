"""vSphere cloud adaptor"""

import functools

tagging_client = None
vapi_connect = None
security_session = None
user_password = None
factories = None
cis_client = None
pyvim_connect = None
std_client = None
downloadsession_client = None
updatesession_client = None
item_client = None
content_client = None
iso_client = None
ovf_client = None
library_items_client = None
vm_template_client = None
vcenter_client = None
library_client = None
content_client = None
vim = None
vmodl = None
pyvmomi = None
pbm = None
vmomi_support = None


def import_package(func):

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global tagging_client, vapi_connect, security_session, user_password, \
            factories, cis_client, pyvim_connect, std_client, \
            downloadsession_client, updatesession_client, item_client, \
            content_client, iso_client, ovf_client, library_items_client, \
            vm_template_client, vcenter_client, library_client, \
            content_client, vim, vmodl, pyvmomi, pbm, vmomi_support
        if tagging_client is None:
            try:
                # pylint: disable=import-outside-toplevel
                from com.vmware import cis_client as _cis_client
                from com.vmware import content_client as _content_client
                from com.vmware import vcenter_client as _vcenter_client
                from com.vmware.cis import tagging_client as _tagging_client
                from com.vmware.content import library_client as _library_client
                from com.vmware.content.library.item import (
                    downloadsession_client as _downloadsession_client)
                from com.vmware.content.library.item import (
                    updatesession_client as _updatesession_client)
                import com.vmware.content.library.item_client as _item_client
                from com.vmware.vapi import std_client as _std_client
                from com.vmware.vcenter import iso_client as _iso_client
                from com.vmware.vcenter import ovf_client as _ovf_client
                from com.vmware.vcenter.vm_template import (
                    library_items_client as _library_items_client)
                import com.vmware.vcenter.vm_template_client as _vm_template_client
                from pyVim import connect as _pyvim_connect
                from pyVmomi import pbm as _pbm
                from pyVmomi import vim as _vim
                from pyVmomi import vmodl as _vmodl
                from pyVmomi import VmomiSupport as _vmomi_support
                import pyVmomi as _pyvmomi
                from vmware.vapi.lib import connect as _vapi_connect
                from vmware.vapi.security import session as _security_session
                from vmware.vapi.security import user_password as _user_password
                from vmware.vapi.stdlib.client import factories as _factories

                tagging_client = _tagging_client
                vapi_connect = _vapi_connect
                security_session = _security_session
                user_password = _user_password
                factories = _factories
                cis_client = _cis_client
                pyvim_connect = _pyvim_connect
                std_client = _std_client
                downloadsession_client = _downloadsession_client
                updatesession_client = _updatesession_client
                item_client = _item_client
                content_client = _content_client
                iso_client = _iso_client
                ovf_client = _ovf_client
                library_items_client = _library_items_client
                vm_template_client = _vm_template_client
                vcenter_client = _vcenter_client
                library_client = _library_client
                vim = _vim
                vmodl = _vmodl
                pyvmomi = _pyvmomi
                pbm = _pbm
                vmomi_support = _vmomi_support

            except ImportError as e:
                raise ImportError('Fail to import dependencies for vSphere.'
                                  'Try pip install "skypilot[vsphere]"'
                                  f'{e}') from None
        return func(*args, **kwargs)

    return wrapper


@import_package
def get_tagging_client():
    return tagging_client


@import_package
def get_vapi_connect():
    return vapi_connect


@import_package
def get_security_session():
    return security_session


@import_package
def get_user_password():
    return user_password


@import_package
def get_factories():
    return factories


@import_package
def get_cis_client():
    return cis_client


@import_package
def get_pyvim_connect():
    return pyvim_connect


@import_package
def get_std_client():
    return std_client


@import_package
def get_downloadsession_client():
    return downloadsession_client


@import_package
def get_updatesession_client():
    return updatesession_client


@import_package
def get_item_client():
    return item_client


@import_package
def get_content_client():
    return content_client


@import_package
def get_iso_client():
    return iso_client


@import_package
def get_ovf_client():
    return ovf_client


@import_package
def get_library_items_client():
    return library_items_client


@import_package
def get_vm_template_client():
    return vm_template_client


@import_package
def get_vcenter_client():
    return vcenter_client


@import_package
def get_library_client():
    return library_client


@import_package
def get_vim():
    return vim


@import_package
def get_vmodl():
    return vmodl


@import_package
def get_pyvmomi():
    return pyvmomi


@import_package
def get_pbm():
    return pbm


@import_package
def get_vmomi_support():
    return vmomi_support
