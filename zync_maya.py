"""
ZYNC Submit

This module provides a maya + Python implementation of the web-based ZYNC
Job Submit GUI. There are a few advantages to doing render submissions to ZYNC
from within maya:
    * extensive preflight checking possible
    * less context switching between the browser and maya

Usage:
    import zync_maya
    zync_maya.submit_dialog()

"""

from functools import partial
import hashlib
import re
import os
import platform
import sys
import time
import shlex

__author__ = 'Alex Schworer'
__copyright__ = 'Copyright 2011, Atomic Fiction, Inc.'

config_path = "%s/config_maya.py" % ( os.path.dirname(__file__), )
if not os.path.exists( config_path ):
    raise Exception( "Could not locate config_maya.py, please create." )
from config_maya import *

required_config = [ "API_DIR", "API_KEY" ]

for key in required_config:
    if not key in globals():
        raise Exception( "config_maya.py must define a value for %s." % ( key, ) )

sys.path.append( API_DIR )
import zync

UI_FILE = "%s/resources/submit_dialog.ui" % ( os.path.dirname( __file__ ), )

import maya.cmds as cmds

def even(num):
    return bool(num % 2)

def odd(num):
    return not bool(num % 2)

def _substitute(parts, tokens, allOrNothing=False, leaveUnmatchedTokens=False):
    result = []
    for i, tok in enumerate(parts):
        if even(i):
            try:
                tokn = tokens[tok]
                if tokn is None:
                    result.append('<%s>' % tok)
                else:
                    result.append(tokn.replace(':', '_'))
            except KeyError:
                if allOrNothing:
                    if leaveUnmatchedTokens:
                        return '<%s>' % tok
                    else:
                        return ''
                elif leaveUnmatchedTokens:
                    result.append('<%s>' % tok)
                else:
                    result.append('')
        else:
            result.append(tok)
    return ''.join(result)

def expandFileTokens(path, tokens, leaveUnmatchedTokens=False):
    """
    path : str
        unexpanded path, containing tokens of the form <MyToken>

    tokens : dict or str
        dictionary of the form {'MyToken' : value} or space separated string of form 'MyToken=value'

    This is a token expansion system based on Maya's, but with several improvements.
    In addition to standard tokens of the form <MyToken>, it also supports
    conditional groups using brackets, which will only be expanded if all the
    tokens within it exist.

    for example, in the following case, the group's contents (the underscore) are
    included because the RenderPass token is filled:

        >>> expandFileTokens('filename[_<RenderPass>].jpg', {'RenderPass' : 'Diffuse'})
        'filename_Diffuse.jpg'

    but in this case the contents enclosed in brackets is dropped:

        >>> expandFileTokens('filename[_<RenderPass>].jpg', {})
        'filename.jpg'
    """
    if isinstance(tokens, basestring):
        tokens = dict([pair.split('=') for pair in shlex.split(tokens)])

    grp_reg = re.compile('\[([^\]]+)\]')
    tok_reg = re.compile('<([a-zA-Z]+)>')
    result = []
    for i, grp in enumerate(grp_reg.split(path)):
        parts = tok_reg.split(grp)
        if even(i):
            result.append(_substitute(parts, tokens, allOrNothing=True, leaveUnmatchedTokens=leaveUnmatchedTokens))
        else:
            result.append(_substitute(parts, tokens, allOrNothing=False, leaveUnmatchedTokens=leaveUnmatchedTokens))
    return ''.join(result)

def generate_scene_path(extra_name=None):
    """
    Returns a hash-embedded scene path with /cloud_submit/ at the end
    of the path, for separation from user scenes.

    TODO: factor out into zync python module
    """
    scene_path = cmds.file(q=True, loc=True)

    scene_dir = os.path.dirname(scene_path)
    cloud_dir = '%s/cloud_submit' % ( scene_dir, )

    if not os.path.exists(cloud_dir):
        os.makedirs(cloud_dir)

    scene_name = os.path.basename(scene_path)

    local_time = time.localtime()

    times = [local_time.tm_mon, local_time.tm_mday, local_time.tm_year,
             local_time.tm_hour, local_time.tm_min, local_time.tm_sec]
    timecode = ''.join(['%02d' % x for x in times])

    old_filename = re.split('.ma', scene_name)[0]
    if extra_name:
        old_filename = '_'.join([old_filename, extra_name])
    to_hash = '_'.join([old_filename, timecode])
    hash = hashlib.md5(to_hash).hexdigest()[-6:]

    # filename will be something like: shotName_comp_v094_37aa20.nk
    new_filename = '_'.join([old_filename, hash]) + '.ma'

    return '%s/%s' % ( cloud_dir, new_filename )

def label_ui(label, ui, *args, **kwargs):
    """
    Helper function that creates an UI element with a text label next to it.
    """
    cmds.text(label=label)
    return getattr(cmds, ui)(*args, **kwargs)

def eval_ui(path, type='textField', **kwargs):
    """
    Returns the value from the given ui element
    """
    return getattr(cmds, type)(path, query=True, **kwargs)

def proj_dir():
    """
    Returns the project dir in the current scene
    """
    return cmds.workspace(q=True, rd=True)

def proj_name():
    """
    Returns the name of the project
    """
    tokens = proj_dir().split(os.path.sep)
    if 'show' in tokens:
        index = tokens.index('show')
        return tokens[index+1]
    else:
        return 'alex_test'

def frame_range():
    """
    Returns the frame-range of the maya scene as a string, like:
        1001-1350
    """
    start = str(int(cmds.getAttr('defaultRenderGlobals.startFrame')))
    end = str(int(cmds.getAttr('defaultRenderGlobals.endFrame')))
    return '%s-%s' % (start, end)

def _file_handler(node):
    """Returns the file referenced by the given node"""
    yield (cmds.getAttr('%s.fileTextureName' % node),)

def _cache_file_handler(node):
    """Returns the files references by the given cacheFile node"""
    path = cmds.getAttr('%s.cachePath' % node)
    cache_name = cmds.getAttr('%s.cacheName' % node)

    yield (os.path.join(path, '%s.mc' % cache_name),
           os.path.join(path, '%s.xml' % cache_name),)

def _diskCache_handler(node):
    """Returns disk caches"""
    yield (cmds.getAttr('%s.cacheName' % node),)

def _vrmesh_handler(node):
    """Handles vray meshes"""
    yield (cmds.getAttr('%s.fileName' % node),)

def _mrtex_handler(node):
    """Handles mentalrayTexutre nodes"""
    yield (cmds.getAttr('%s.fileTextureName' % node),)

def _gpu_handler(node):
    """Handles gpuCache nodes"""
    yield (cmds.getAttr('%s.cacheFileName' % node),)

def _mrOptions_handler(node):
    """Handles mentalrayOptions nodes, for Final Gather"""
    mapName = cmds.getAttr('%s.finalGatherFilename' % node).strip()
    if mapName != "":
        path = cmds.workspace(q=True, rd=True)
        if path[-1] != "/":
            path += "/"
        path += "renderData/mentalray/finalgMap/"
        path += mapName
        if not mapName.endswith( ".fgmap" ):
            path += ".fgmap"
        yield (path,)

def _mrIbl_handler(node):
    """Handles mentalrayIblShape nodes"""
    yield (cmds.getAttr('%s.texture' % node),)

def _abc_handler(node):
    """Handles AlembicNode nodes"""
    yield (cmds.getAttr('%s.abc_File' % node),)

def _vrSettings_handler(node):
    """Handles VRaySettingsNode nodes, for irradiance map"""
    yield(cmds.getAttr('%s.ifile' % node),)

def get_scene_files():
    """Returns all of the files being used by the scene"""
    file_types = {'file': _file_handler,
                  'cacheFile': _cache_file_handler,
                  'diskCache': _diskCache_handler,
                  'VRayMesh': _vrmesh_handler,
                  'mentalrayTexture': _mrtex_handler,
                  'gpuCache': _gpu_handler,
                  'mentalrayOptions': _mrOptions_handler,
                  'mentalrayIblShape': _mrIbl_handler,
                  'AlembicNode': _abc_handler,
                  'VRaySettingsNode': _vrSettings_handler }

    for file_type in file_types:
        handler = file_types.get(file_type)
        nodes = cmds.ls(type=file_type)
        for node in nodes:
            for files in handler(node):
                for scene_file in files:
                    if scene_file != None:
                        yield scene_file.replace('\\', '/')

def get_default_extension(renderer):
    """Returns the filename prefix for the given renderer, either mental ray
       or maya software.
    """
    if renderer == zync.SOFTWARE_RENDERER:
        menu_grp = 'imageMenuMayaSW'
    elif renderer == zync.MENTAL_RAY_RENDERER:
        menu_grp = 'imageMenuMentalRay'
    else:
        raise Exception('Invalid Renderer: %s' % renderer)
    try:
        val = cmds.optionMenuGrp(menu_grp, q=True, v=True)
    except RuntimeError:
        msg = 'Please open the Maya Render globals before submitting.'
        raise Exception(msg)
    else:
        return val.split()[-1][1:-1]

def get_layer_override(layer, node, attribute='imageFilePrefix'):
    """Helper method to return the layer override value for the given node and attribute"""
    cur_layer = cmds.editRenderLayerGlobals(q=True, currentRenderLayer=True)

    cmds.editRenderLayerGlobals(currentRenderLayer=layer)
    attr = '.'.join([node, attribute])
    layer_override = cmds.getAttr(attr)
    cmds.editRenderLayerGlobals(currentRenderLayer=cur_layer)
    return layer_override

def get_pass_names(renderer, layer):
    """Helper method to return the passes for a given layer"""
    cur_layer = cmds.editRenderLayerGlobals(q=True, currentRenderLayer=True)
    cmds.editRenderLayerGlobals(currentRenderLayer=layer)

    pass_names = []
    if renderer == zync.VRAY_RENDERER:
        render_element_nodes = cmds.ls(type="VRayRenderElement")
        for element in render_element_nodes:
            if cmds.getAttr(element + '.enabled'):
                element_attrs = cmds.listAttr(element)
                vray_name_attr = [ x for x in element_attrs if re.match('vray_name_.*', x) or re.match('vray_filename_.*', x) ]
                pass_names.append(cmds.getAttr(element + '.' +vray_name_attr[0]))

    if renderer == zync.MENTAL_RAY_RENDERER:
        pass_names.append('MasterBeauty')
        render_pass_nodes = cmds.listConnections(layer + '.renderPass')
        if render_pass_nodes:
            for pass_ in render_pass_nodes:
                print "  pass %s for %s" % (pass_, layer)
                if cmds.getAttr(pass_ + '.renderable'):
                    pass_names.append(pass_)

    cmds.editRenderLayerGlobals(currentRenderLayer=cur_layer)

    return pass_names

def create_local_paths(params):
    """Creates a local file hierarchy to assist download of rendered frames with
    a non standard file prefix."""

    base_out_path = params['out_path']

    # set render path for defaultRenderLayer *can't be deleted*
    layer_paths = {}
    default = os.path.dirname(os.path.join(base_out_path, params['scene_info']['file_prefix'][0]))
    layer_paths['defaultRenderLayer'] = expandFileTokens(default, {'Layer':'masterLayer', 'RenderLayer':'masterLayer'}, leaveUnmatchedTokens=True)

    # set render path for remaining layers
    for path in params['scene_info']['file_prefix'][1:]:
        for k,v in path.iteritems():
            raw_layer_dir = os.path.dirname(os.path.join(base_out_path, v))
            layer_paths[k] = expandFileTokens(raw_layer_dir, {'Layer': k, 'RenderLayer': k}, leaveUnmatchedTokens=True)

    # next for each active layer create the directory path for the layer. This is appended to the end of the path
    # in vray. MRay allows us to put RenderPass where ever we like.
    for layer in params['selected_layers']:
        # for each active pass attached to the layer, create the dir path
        for pass_name in params['scene_info']['layer_passes'].get(layer, []):
            if params['renderer'] == zync.VRAY_RENDERER:
                pass_path = os.path.join(layer_paths[layer], pass_name)

            elif params['renderer'] in (zync.MENTAL_RAY_RENDERER, zync.SOFTWARE_RENDERER):
                pass_path = expandFileTokens(layer_paths[layer], {'RenderPass': pass_name})
            else:
                pass_path = layer_paths[layer]

            print "  " + pass_path
            if not os.path.exists(pass_path):
                os.makedirs(pass_path)

    if not params['scene_info']['layer_passes'].get(layer, []):
        print "no passes selected for layer. creating path for layer only."
        if not os.path.exists(layer_paths[layer]):
            os.makedirs(layer_paths[layer])

class MayaZyncException(Exception):
    """
    This exception issues a Maya warning.
    """
    def __init__(self, msg, *args, **kwargs):
        cmds.warning(msg)
        super(MayaZyncException, self).__init__(msg, *args, **kwargs)

class SubmitWindow(object):
    """
    A Maya UI window for submitting to ZYNC
    """
    def __init__(self, title='ZYNC Submit', path_mappings=()):
        """
        Constructs the window.
        You must call show() to display the window.

        Path mappings: Replacements to apply for transforming paths,
                       a list of 2-tuples:
                        [ ('/From_Path', '/to_path') ]

        """
        self.title = title
        self.path_mappings = path_mappings

        scene_name = cmds.file(q=True, loc=True)
        if scene_name == 'unknown':
            cmds.error( 'Please save your script before launching a job.' )

        project_response = zync.get_project_name( scene_name )
        if project_response["code"] != 0:
            cmds.error( project_response["response"] )
        self.project_name = project_response["response"]
        self.num_instances = 1
        self.priority = 50
        self.parent_id = None

        self.project = proj_dir()
        if self.project[-1] == "/":
            self.project = self.project[:-1]

        maya_output_response = zync.get_maya_output_path( scene_name )
        if maya_output_response["code"] != 0:
            cmds.error( maya_output_response["response"] )
        self.output_dir =  maya_output_response["response"]

        self.frange = frame_range()
        self.frame_step = cmds.getAttr('defaultRenderGlobals.byFrameStep')
        self.chunk_size = 10
        self.upload_only = 0
        self.start_new_slots = 0
        self.skip_check = 0
        self.notify_complete = 0
        self.vray_nightly = 0
        self.use_vrscene = 0

        self.init_layers()

        self.x_res = cmds.getAttr('defaultResolution.width')
        self.y_res = cmds.getAttr('defaultResolution.height')

        self.username = ''
        self.password = ''

        self.name = self.loadUI(UI_FILE)

        self.check_references()

    def loadUI(self, ui_file):
        """
        Loads the UI and does an post-load commands
        """

        # monkey patch the cmds module for use when the UI gets loaded
        cmds.submit_callb = partial(self.get_initial_value, self)
        cmds.do_submit_callb = partial(self.submit, self)

        if cmds.window('SubmitDialog', q=True, ex=True):
            cmds.deleteUI('SubmitDialog')
        name = cmds.loadUI(f=ui_file)

        cmds.textScrollList('layers', e=True, append=self.layers)

        # callbacks
        cmds.checkBox('upload_only', e=True, changeCommand=self.upload_only_toggle)
        cmds.optionMenu('renderer', e=True, changeCommand=self.change_renderer)
        self.change_renderer( self.renderer )

        return name

    def upload_only_toggle( self, checked ):
        if checked:
            cmds.textField('num_instances', e=True, en=False)
            cmds.optionMenu('instance_type', e=True, en=False)
            cmds.checkBox('start_new_slots', e=True, en=False)
            cmds.checkBox('skip_check', e=True, en=False)
            cmds.textField('output_dir', e=True, en=False)
            cmds.optionMenu('renderer', e=True, en=False)
            cmds.checkBox('vray_nightly', e=True, en=False)
            cmds.checkBox('use_vrscene', e=True, en=False)
            cmds.textField('frange', e=True, en=False)
            cmds.textField('frame_step', e=True, en=False)
            cmds.textField('chunk_size', e=True, en=False)
            cmds.optionMenu('camera', e=True, en=False)
            cmds.textScrollList('layers', e=True, en=False)
            cmds.textField('x_res', e=True, en=False)
            cmds.textField('y_res', e=True, en=False)
        else:
            cmds.textField('num_instances', e=True, en=True)
            cmds.optionMenu('instance_type', e=True, en=True)
            cmds.checkBox('start_new_slots', e=True, en=True)
            cmds.checkBox('skip_check', e=True, en=True)
            cmds.textField('output_dir', e=True, en=True)
            cmds.optionMenu('renderer', e=True, en=True)
            cmds.checkBox('vray_nightly', e=True, en=True)
            cmds.checkBox('use_vrscene', e=True, en=True)
            cmds.textField('frange', e=True, en=True)
            cmds.textField('frame_step', e=True, en=True)
            cmds.textField('chunk_size', e=True, en=True)
            cmds.optionMenu('camera', e=True, en=True)
            cmds.textScrollList('layers', e=True, en=True)
            cmds.textField('x_res', e=True, en=True)
            cmds.textField('y_res', e=True, en=True)

    def change_renderer( self, renderer ):
        if renderer in ("vray", "V-Ray"):
            cmds.checkBox('vray_nightly', e=True, en=True)
            cmds.checkBox('use_vrscene', e=True, en=True)
        else:
            cmds.checkBox('vray_nightly', e=True, en=False)
            cmds.checkBox('use_vrscene', e=True, en=False)

    def check_references(self):
        """
        Run any checks to ensure all reference files are accurate. If not,
        raise an Exception to halt the submit process.

        This function currently does nothing. Before Maya Binary was supported
        it checked to ensure no .mb files were being used.
        """

        #for ref in cmds.file(q=True, r=True):
        #    if check_failed:
        #        raise Exception(msg)
        pass

    def get_render_params(self):
        """
        Returns a dict of all the render parameters set on the UI
       """
        params = dict()

        params['proj_name'] = eval_ui('project_name', text=True)
        parent = eval_ui('parent_id', text=True).strip()
        if parent != None and parent != "":
            params['parent_id'] = parent
        params['upload_only'] = int(eval_ui('upload_only', 'checkBox', v=True))
        params['start_new_slots'] = int( not eval_ui('start_new_slots', 'checkBox', v=True) )
        params['skip_check'] = int(eval_ui('skip_check', 'checkBox', v=True))
        params['notify_complete'] = int(eval_ui('notify_complete', 'checkBox', v=True))
        params['project'] = eval_ui('project', text=True)
        params['out_path'] = eval_ui('output_dir', text=True)
        render = eval_ui('renderer', type='optionMenu', v=True)

        for k in zync.MAYA_RENDERERS:
            if zync.MAYA_RENDERERS[k] == render:
                params['renderer'] = k
                break
        else:
            params['renderer'] = zync.MAYA_DEFAULT_RENDERER

        params['num_instances'] = int(eval_ui('num_instances', text=True))

        selected_type = eval_ui('instance_type', 'optionMenu', v=True)
        for inst_type in zync.INSTANCE_TYPES:
            if selected_type.startswith( inst_type ):
                params['instance_type'] = zync.INSTANCE_TYPES[inst_type]['csp_label']
                break
        else:
            params['instance_type'] = zync.DEFAULT_INSTANCE_TYPE

        params['frange'] = eval_ui('frange', text=True)
        params['step'] = int(eval_ui('frame_step', text=True))
        params['chunk_size'] = int(eval_ui('chunk_size', text=True))
        params['camera'] = eval_ui('camera', 'optionMenu', v=True)
        params['xres'] = int(eval_ui('x_res', text=True))
        params['yres'] = int(eval_ui('y_res', text=True))

        if params['upload_only'] == 0 and params['renderer'] == 'vray':
            params['vray_nightly'] = int(eval_ui('vray_nightly', 'checkBox', v=True))
            params['use_vrscene'] = int(eval_ui('use_vrscene', 'checkBox', v=True))
        else:
            params['vray_nightly'] = 0
            params['use_vrscene'] = 0

        return params

    def show(self):
        """
        Displays the window.
        """
        cmds.showWindow(self.name)

    def init_instance_type(self):
        non_default = []
        for inst_type in zync.INSTANCE_TYPES:
            if inst_type == zync.DEFAULT_INSTANCE_TYPE:
                cmds.menuItem( parent='instance_type', label='%s (%s)' % ( inst_type, zync.INSTANCE_TYPES[inst_type]["description"] ) )
            else:
                non_default.append( '%s (%s)' % ( inst_type, zync.INSTANCE_TYPES[inst_type]["description"] ) )
        for label in non_default:
            cmds.menuItem( parent='instance_type', label=label )

    def init_renderer(self):
        # put default renderer first
        default_renderer_name = zync.MAYA_RENDERERS[zync.MAYA_DEFAULT_RENDERER]
        cmds.menuItem(parent='renderer',
            label=default_renderer_name)

        for item in zync.MAYA_RENDERERS.values():
            if item != default_renderer_name:
                cmds.menuItem(parent='renderer', label=item)

        self.renderer = zync.MAYA_DEFAULT_RENDERER

    def init_camera(self):
        cam_parents = [cmds.listRelatives(x, ap=True)[-1] for x in cmds.ls(cameras=True)]
        for cam in cam_parents:
            if ( cmds.getAttr( cam + '.renderable') ) == True:
                cmds.menuItem( parent='camera', label=cam )

    def init_layers(self):
        self.layers = []
        try:
            all_layers = cmds.ls(type='renderLayer',showNamespace=True)
            for i in range( 0, len(all_layers), 2 ):
                if all_layers[i+1] == ':':
                    self.layers.append( all_layers[i] )
        except Exception:
            self.layers = cmds.ls(type='renderLayer')

    def get_scene_info(self, renderer):
        """
        Returns scene info for the current scene.
        We use this to allow ZYNC to skip the file checks.

        """
        layers = [x for x in cmds.ls(type='renderLayer')\
                  if x != 'defaultRenderLayer' and not ':' in x]
        references = cmds.file(q=True, r=True)

        layer_prefixes = dict()
        layer_passes = dict()
        for layer in layers:
            if renderer == zync.VRAY_RENDERER:
                node = 'vraySettings'
                attribute = 'fileNamePrefix'
                format_attr = 'imageFormatStr'
            elif renderer in (zync.SOFTWARE_RENDERER, zync.MENTAL_RAY_RENDERER):
                node = 'defaultRenderGlobals'
                attribute = 'imageFilePrefix'
            try:
                layer_prefix = get_layer_override(layer, node, attribute)
                layer_prefixes[layer] = layer_prefix
            except Exception:
                pass

            if renderer in (zync.VRAY_RENDERER, zync.MENTAL_RAY_RENDERER):
                passes = get_pass_names(renderer, layer)
                layer_passes[layer] = passes

        if renderer == zync.VRAY_RENDERER:
            extension = cmds.getAttr('vraySettings.imageFormatStr')
            if extension == None:
                extension = 'png'
            padding = int(cmds.getAttr('vraySettings.fileNamePadding'))
            global_prefix = get_layer_override('defaultRenderLayer', 'vraySettings', 'fileNamePrefix')
        elif renderer in (zync.SOFTWARE_RENDERER, zync.MENTAL_RAY_RENDERER):
            extension = get_default_extension(renderer)
            padding = int(cmds.getAttr('defaultRenderGlobals.extensionPadding'))
            global_prefix = get_layer_override('defaultRenderLayer', 'defaultRenderGlobals', 'imageFilePrefix')

        extension = extension[:3]

        file_prefix = [global_prefix]
        file_prefix.append(layer_prefixes)
        files = list(set(get_scene_files()))

        plugins = []
        plugin_list = cmds.pluginInfo( query=True, pluginsInUse=True )
        for i in range( 0, len(plugin_list), 2):
            plugins.append( str(plugin_list[i]) )

        if len(cmds.ls(type='cacheFile')) > 0:
            plugins.append( "cache" )

        scene_info = {'files': files,
                      'render_layers': self.layers,
                      'references': references,
                      'file_prefix': file_prefix,
                      'padding': padding,
                      'extension': extension,
                      'plugins': plugins,
                      'layer_passes': layer_passes}
        return scene_info

    @staticmethod
    def get_initial_value(window, name):
        """
        Returns the initial value for a given attribute
        """
        init_name = '_'.join(('init', name))
        if hasattr(window, init_name):
            return getattr(window, init_name)()
        elif hasattr(window, name):
            return getattr(window, name)
        else:
            return 'Undefined'

    @staticmethod
    def submit(window):
        """
        Submits to zync
        """
        params = window.get_render_params()

        scene_path = cmds.file(q=True, loc=True)
        # Comment out the line above and uncomment this section if you want to
        # save a unique copy of the scene file each time your submit a job.
        '''
        original_path = cmds.file(q=True, loc=True)
        original_modified = cmds.file(q=True, modified=True)
        scene_path = generate_scene_path()
        cmds.file( rename=scene_path )
        cmds.file( save=True, type='mayaAscii' )
        cmds.file( rename=original_path )
        cmds.file( modified=original_modified )
        '''

        if params["upload_only"] == 1:
            layers = None
        else:
            layers = eval_ui('layers', 'textScrollList', ai=True, si=True)
            if not layers:
                msg = 'Please select layer(s) to render.'
                raise MayaZyncException(msg)
            params['selected_layers'] = layers
            layers = ','.join(layers)

        username = eval_ui('username', text=True)
        password = eval_ui('password', text=True)
        if username=='' or password=='':
            msg = 'Please enter a ZYNC username and password.'
            raise MayaZyncException(msg)

        try:
            z = zync.Zync( "maya_plugin", API_KEY, username=username, password=password )
        except zync.ZyncAuthenticationError, e:
            msg = 'ZYNC Username Authentication Failed'
            raise MayaZyncException(msg)

        if params["upload_only"] == 1:
            params['scene_info'] = {}
        else:
            scene_info = window.get_scene_info(params['renderer'])
            params['scene_info'] = scene_info

        z.add_path_mappings(window.path_mappings)

        import pprint
        pp = pprint.PrettyPrinter()
        print pp.pprint(params)

        if params['upload_only'] == 0:
            create_local_paths(params)
        del params['selected_layers']
        del params['scene_info']['layer_passes']

        z.submit_job("maya", scene_path, layers, params=params)

        cmds.confirmDialog(title='Success',

        message='Job submitted to ZYNC.\n\nPlease ensure your Client App is running and logged in so your job can start.',
        button='OK',
        defaultButton='OK')


def submit_dialog():
    submit_window = SubmitWindow()
    submit_window.show()

