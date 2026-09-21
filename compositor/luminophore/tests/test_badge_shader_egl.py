"""Production badge shader pixels in software EGL, without desktop access."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class BadgeShaderTests(unittest.TestCase):
    def test_silhouette_glow_and_tint(self):
        root = Path(__file__).resolve().parents[2]
        source = (root/'src/render/luminophore/LuminophoreSpatialBadgeRenderer.cpp').read_text()
        fragment = source.split('R"GLSL(',1)[1].split(')GLSL"',1)[0]
        setup = Path(__file__).with_name('bloom_scissor_egl.c').read_text()
        setup = setup[:setup.index('    GLuint q;')]
        vertex = '#version 300 es\nin vec2 pos; out vec2 v_texcoord; void main(){gl_Position=vec4(pos,0,1);v_texcoord=(pos+1.0)*0.5;}'
        program = "#include <GLES3/gl3.h>\n" + setup + '\nconst char* vs='+json.dumps(vertex)+';\nconst char* fs='+json.dumps(fragment)+';\n' + r'''
        char error[256]={0};
        GLuint v=luminophore_bloom_compile(GL_VERTEX_SHADER,vs,error,sizeof(error));
        GLuint f=luminophore_bloom_compile(GL_FRAGMENT_SHADER,fs,error,sizeof(error));
        if(!v || !f){ puts(error); return 10; }
        GLuint p=glCreateProgram(); glAttachShader(p,v); glAttachShader(p,f); glLinkProgram(p);
        GLint linked=0; glGetProgramiv(p,GL_LINK_STATUS,&linked); if(!linked)return 11;
        GLuint output, fb, mask, buffer;
        glGenTextures(1,&output); glBindTexture(GL_TEXTURE_2D,output);
        glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,128,128,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL);
        glGenFramebuffers(1,&fb); glBindFramebuffer(GL_FRAMEBUFFER,fb);
        glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,output,0);
        unsigned char pixels[4096]={0};
        for(int y=16;y<48;y++)for(int x=30;x<34;x++)pixels[y*64+x]=255;
        glGenTextures(1,&mask); glBindTexture(GL_TEXTURE_2D,mask);
        glTexImage2D(GL_TEXTURE_2D,0,GL_R8,64,64,0,GL_RED,GL_UNSIGNED_BYTE,pixels);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
        const float quad[]={-1,-1,1,-1,-1,1,1,1};
        glGenBuffers(1,&buffer);glBindBuffer(GL_ARRAY_BUFFER,buffer);glBufferData(GL_ARRAY_BUFFER,sizeof(quad),quad,GL_STATIC_DRAW);
        glUseProgram(p);GLint pos=glGetAttribLocation(p,"pos");glEnableVertexAttribArray(pos);glVertexAttribPointer(pos,2,GL_FLOAT,0,0,0);
        glUniform1i(glGetUniformLocation(p,"tex"),0);glUniform1f(glGetUniformLocation(p,"alpha"),1);
        glUniform4f(glGetUniformLocation(p,"color"),0.25,0.5,1,1);
        glViewport(0,0,128,128);glDrawArrays(GL_TRIANGLE_STRIP,0,4);
        unsigned char image[128*128*4];glReadPixels(0,0,128,128,GL_RGBA,GL_UNSIGNED_BYTE,image);
        int core=(64*128+64)*4, halo=(64*128+68)*4;
        if(image[core+3]<200 || image[halo+3]==0 || image[3]!=0)return 12;
        if(image[halo+2]<=image[halo+1] || image[halo+1]<=image[halo])return 13;
        if(glGetError()!=GL_NO_ERROR)return 14;
        eglMakeCurrent(d,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);eglDestroyContext(d,c);eglTerminate(d);
        return 0;
        }
        '''
        with tempfile.TemporaryDirectory(prefix='luminophore-badge-egl-') as directory:
            src = Path(directory)/'test.c'; src.write_text(program)
            binary = str(Path(directory)/'test')
            subprocess.run(['cc',str(src),'-I',str(root/'src/render/luminophore'),'-lEGL','-lGLESv2','-lm','-o',binary],check=True)
            result=subprocess.run([binary],env={**os.environ,'LIBGL_ALWAYS_SOFTWARE':'1'},capture_output=True,text=True,timeout=30)
            if result.returncode in (2,3):self.skipTest('software EGL unavailable')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
